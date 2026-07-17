"""FR-17 / M6 retest-session persistence + orchestration (ADR-0025, Slice 0).

An agentic retest session is a :class:`~revalid.db.RetestSessionRecord` row plus
its append-only transcript of :class:`~revalid.db.SessionEventRecord` rows,
symmetric with how :mod:`revalid.findings` splits identity from immutable
history (Task 3: ``create_session``, ``append_event``, ``load_events_after``,
``set_status``, ``record_verdict``).

Task 5 adds the orchestration layer that drives the Task 4 agent
(:mod:`revalid.retest_agent`) step-by-step: a process-local
:class:`SessionRegistry` of :class:`LiveSession` state, ``start_and_step``/
``apply_decision`` to pause on each proposed command for human approval and
resume it, and a step-budget backstop that force-concludes a session that
never stops proposing commands.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import ModelMessage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revalid.db import RetestSessionRecord, SessionEventRecord
from revalid.domain import RetestSessionStatus, SessionEventKind, VerdictStatus
from revalid.retest_agent import ConcludeOutput, RetestSessionDeps, format_observations
from revalid.sandbox import CommandResult, Sandbox

_TERMINAL: frozenset[RetestSessionStatus] = frozenset(
    {
        RetestSessionStatus.CONCLUDED,
        RetestSessionStatus.GIVEN_UP,
        RetestSessionStatus.ENDED,
        RetestSessionStatus.ERROR,
    }
)


def is_terminal(status: RetestSessionStatus) -> bool:
    """Return whether ``status`` is one of the terminal retest-session states.

    Args:
        status: The status to test.

    Returns:
        ``True`` for ``concluded``/``given_up``/``ended``/``error``.
    """
    return status in _TERMINAL


def create_session(
    session: Session,
    *,
    finding_id: int,
    model: str,
    free_launch: bool = False,
    max_steps: int = 8,
    max_seconds: int | None = None,
) -> RetestSessionRecord:
    """Insert a ``starting`` session row and return it.

    Args:
        session: Active DB session.
        finding_id: The finding identity (FR-16) this session retests.
        model: The resolved LLM model string driving the agent.
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (plan changes stay gated regardless). FR-17 Slice 5.
        max_steps: Step budget — commands approved before force-conclude.
        max_seconds: Wall-clock budget in seconds, enforced only in free-launch
            mode; ``None`` means no time bound.
    """
    record = RetestSessionRecord(
        finding_id=finding_id,
        status=RetestSessionStatus.STARTING.value,
        model=model,
        free_launch=free_launch,
        max_steps=max_steps,
        max_seconds=max_seconds,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _next_seq(session: Session, session_id: int) -> int:
    """Return the next monotonic transcript sequence number for a session."""
    seqs = session.scalars(
        select(SessionEventRecord.seq).where(SessionEventRecord.session_id == session_id)
    ).all()
    return (max(seqs) + 1) if seqs else 1


def append_event(
    session: Session, session_id: int, kind: SessionEventKind, payload: dict[str, Any]
) -> SessionEventRecord:
    """Append one transcript event with the next ``seq`` and commit."""
    event = SessionEventRecord(
        session_id=session_id, seq=_next_seq(session, session_id), kind=kind.value, payload=payload
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def load_events_after(session: Session, session_id: int, after_seq: int) -> list[dict[str, Any]]:
    """Return transcript events with ``seq > after_seq`` in order, as plain dicts."""
    rows = session.scalars(
        select(SessionEventRecord)
        .where(SessionEventRecord.session_id == session_id, SessionEventRecord.seq > after_seq)
        .order_by(SessionEventRecord.seq)
    ).all()
    return [{"seq": r.seq, "kind": r.kind, "payload": r.payload} for r in rows]


def set_status(session: Session, session_id: int, status: RetestSessionStatus) -> None:
    """Move a session to ``status`` and record a ``state_change`` transcript event."""
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    record.status = status.value
    session.commit()
    append_event(session, session_id, SessionEventKind.STATE_CHANGE, {"to": status.value})


def record_verdict(
    session: Session, session_id: int, status: VerdictStatus, rationale: str
) -> None:
    """Persist the agent verdict on the session row + its transcript events.

    Appends the ``VERDICT`` event, then a ``STATE_CHANGE`` event to
    ``concluded``, BOTH before committing the terminal row (event-before-
    terminal-status invariant, cf. ``_fail`` which already appends its
    ``ERROR`` event before ``set_status``). The WS stream handler closes on
    ``terminal AND no new events``, polling concurrently with this function on
    a separate DB session; without this ordering a poll landing between the
    event appends and the row commit could observe the terminal status with
    one of the events not yet visible, and close the stream without ever
    sending it.

    The ``STATE_CHANGE`` event is required because the frontend derives the
    session's displayed status only from the latest such event, not from
    polling the row directly — without it, a normal conclude would leave the
    UI showing the pre-verdict status forever. The budget-exhaustion caller
    (``apply_decision``) calls this and then ``_mark_given_up``, which appends
    its own ``STATE_CHANGE`` to ``given_up`` right after; the latest one wins,
    so that path correctly ends up ``given_up`` rather than ``concluded``.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    append_event(
        session,
        session_id,
        SessionEventKind.VERDICT,
        {"status": status.value, "rationale": rationale},
    )
    append_event(
        session,
        session_id,
        SessionEventKind.STATE_CHANGE,
        {"to": RetestSessionStatus.CONCLUDED.value},
    )
    record.status = RetestSessionStatus.CONCLUDED.value
    record.verdict_status = status.value
    record.verdict_rationale = rationale
    record.ended_at = func.now()
    session.commit()


@dataclass
class LiveSession:
    """In-memory live state for one active session (not restart-safe, Slice 0).

    Deliberately does NOT cache a :class:`~revalid.retest_agent.RetestSessionDeps`:
    ``start_and_step`` and ``apply_decision`` may run in separate background
    tasks against separate DB sessions (Task 6's async driver), and deps'
    ``emit_output`` closure captures whichever ``Session`` built it. Caching
    deps here would let a later call write ``command_output`` events through
    an already-closed session. Callers build deps fresh via ``_make_deps``
    immediately before each ``agent.run_sync``.

    Attributes:
        agent: The built retest agent driving this session.
        sandbox: The persistent sandbox handle for this session's lifetime.
        messages: The full Pydantic AI message history so far (resumed on
            each step via ``message_history``).
        pending_call_id: The ``tool_call_id`` awaiting a human decision, or
            ``None`` when no command is currently proposed.
        step_count: Number of commands approved (and run) so far.
        max_steps: The maximum number of commands the agent may run before
            the session is force-concluded ``inconclusive`` (budget backstop).
        lock: Guards the compare-and-swap on ``pending_call_id`` in
            ``apply_decision`` so two concurrent decisions (e.g. a double-click
            on Approve before the REST 202 re-enables the button) can't both
            observe the same pending call and both resume the agent run.
    """

    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]
    sandbox: Sandbox
    messages: list[ModelMessage] = field(default_factory=list)
    pending_call_id: str | None = None
    #: What the pending approval is for: ``"command"`` (run a command) or
    #: ``"plan"`` (a guiding-plan change, FR-17 Slice 3). Set when a proposal is
    #: emitted; read by ``apply_decision`` to record the matching approval event
    #: and to exempt plan approvals from the command step-budget.
    pending_kind: str = "command"
    step_count: int = 0
    max_steps: int = 8
    #: Whether the agent's commands auto-run without a per-command human approval
    #: (FR-17 Slice 5). Plan changes stay gated regardless. Toggled live by
    #: ``set_free_launch``; the free-launch loop lives in ``_drive_auto``.
    free_launch: bool = False
    #: Wall-clock budget in seconds (free-launch only); ``None`` = no time bound.
    max_seconds: float | None = None
    #: The wall-clock budget's origin, stamped by ``start_and_step`` from its
    #: injected ``clock`` (``time.monotonic`` in production). The default is a
    #: safe fallback for a directly-constructed live session.
    started_at: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Manual operator commands (`!`) run since the agent's last turn, buffered
    #: here and surfaced to the agent on its next turn so it observes what the
    #: human did (FR-17 Slice 2). Guarded by ``lock`` — appended by the human-
    #: command worker, drained by the next agent resume, on separate threads.
    observations: list[str] = field(default_factory=list)

    def observe(self, summary: str) -> None:
        """Buffer one operator-command summary for the agent's next turn (thread-safe)."""
        with self.lock:
            self.observations.append(summary)

    def drain(self) -> list[str]:
        """Atomically return and clear the buffered operator observations (thread-safe)."""
        with self.lock:
            drained = list(self.observations)
            self.observations.clear()
            return drained

    #: Free-text operator chat messages (FR-17 Slice 4) queued since the agent's
    #: last turn. Delivered as a first-class ``user_prompt`` on the next agent
    #: resume (approve/reject) — the operator's *voice*, distinct from
    #: ``observations`` (`!` command *results* folded into a tool return).
    #: Guarded by ``lock`` — appended by the message worker, drained by the next
    #: agent resume, on separate threads.
    human_messages: list[str] = field(default_factory=list)

    def receive_message(self, text: str) -> None:
        """Queue one operator chat message for the agent's next turn (thread-safe)."""
        with self.lock:
            self.human_messages.append(text)

    def drain_messages(self) -> list[str]:
        """Atomically return and clear the queued operator chat messages (thread-safe)."""
        with self.lock:
            drained = list(self.human_messages)
            self.human_messages.clear()
            return drained


class SessionRegistry:
    """Process-local registry of live sessions (one per app instance)."""

    def __init__(self) -> None:
        """Start with an empty registry."""
        self._live: dict[int, LiveSession] = {}

    def put(self, session_id: int, live: LiveSession) -> None:
        """Register ``live`` as the active state for ``session_id``."""
        self._live[session_id] = live

    def get(self, session_id: int) -> LiveSession | None:
        """Return the live state for ``session_id``, or ``None`` if not live."""
        return self._live.get(session_id)

    def drop(self, session_id: int) -> None:
        """Remove ``session_id`` from the registry (no-op if already absent)."""
        self._live.pop(session_id, None)


def _make_deps(session: Session, session_id: int, live: LiveSession) -> RetestSessionDeps:
    """Build agent deps whose callbacks append output + surface operator activity.

    Rebuilt fresh, immediately before every ``agent.run_sync`` call — see the
    correctness note on :class:`LiveSession`. The returned ``emit_output``
    closure is only ever invoked synchronously inside that same call, so
    binding it to ``session`` here is safe. ``drain_observations`` drains any
    manual operator commands buffered on ``live`` (under its lock) so the
    agent's ``run_command`` tool can append them to the result it reads.
    """

    def emit(command: str, result: CommandResult) -> None:
        append_event(
            session,
            session_id,
            SessionEventKind.COMMAND_OUTPUT,
            {
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "elapsed_ms": result.elapsed_ms,
            },
        )

    def emit_plan(steps: list[str]) -> None:
        append_event(session, session_id, SessionEventKind.PLAN_UPDATED, {"steps": steps})

    def emit_message(message: str) -> None:
        append_event(session, session_id, SessionEventKind.AGENT_MESSAGE, {"text": message})

    return RetestSessionDeps(
        sandbox=live.sandbox,
        emit_output=emit,
        drain_observations=live.drain,
        emit_plan=emit_plan,
        emit_message=emit_message,
    )


def _emit_proposal(
    session: Session, session_id: int, live: LiveSession, call: Any
) -> RetestSessionStatus:
    """Record a proposed command or plan change and return the awaiting status.

    Branches on the gated tool the agent called: ``run_command`` becomes a
    ``command_proposed`` event (awaiting a command decision), ``set_plan`` a
    ``plan_proposed`` event (awaiting a plan decision). ``live.pending_kind``
    tags which, so :func:`apply_decision` records the matching approval event
    and only command approvals count against the step budget.
    """
    args = call.args_as_dict()
    live.pending_call_id = call.tool_call_id
    if call.tool_name == "set_plan":
        live.pending_kind = "plan"
        append_event(
            session,
            session_id,
            SessionEventKind.PLAN_PROPOSED,
            {
                "steps": args["steps"],
                "rationale": args["rationale"],
                "tool_call_id": call.tool_call_id,
            },
        )
        return RetestSessionStatus.AWAITING_PLAN
    live.pending_kind = "command"
    append_event(
        session,
        session_id,
        SessionEventKind.COMMAND_PROPOSED,
        {
            "command": args["command"],
            "rationale": args["rationale"],
            "tool_call_id": call.tool_call_id,
        },
    )
    return RetestSessionStatus.AWAITING_COMMAND


def _dispatch_output(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    result: AgentRunResult[ConcludeOutput | DeferredToolRequests],
) -> None:
    """Persist the outcome of one agent step: a proposed command or a verdict.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (to update transcript/pending state).
        session_id: The retest session this step belongs to.
        result: The run result just produced by ``agent.run_sync``.
    """
    live = registry.get(session_id)
    if live is None:
        return
    live.messages = result.all_messages()
    output = result.output
    if isinstance(output, DeferredToolRequests) and output.approvals:
        awaiting = _emit_proposal(session, session_id, live, output.approvals[0])
        set_status(session, session_id, awaiting)
    elif isinstance(output, ConcludeOutput):
        record_verdict(session, session_id, output.status, output.rationale)
        _teardown(registry, session_id)


def _teardown(registry: SessionRegistry, session_id: int) -> None:
    """Stop the sandbox (if live) and drop the session from the registry."""
    live = registry.get(session_id)
    if live is not None:
        live.sandbox.stop()
        registry.drop(session_id)


def start_and_step(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests],
    sandbox: Sandbox,
    finding_prompt: str,
    *,
    max_steps: int = 8,
    free_launch: bool = False,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Start the sandbox and run the retest agent's first step.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry; a new :class:`LiveSession` is
            registered here for ``session_id``.
        session_id: The already-created (``starting``) retest session to drive.
        agent: The built retest agent (Task 4).
        sandbox: The not-yet-started sandbox for this session.
        finding_prompt: The user prompt describing the finding to retest.
        max_steps: Maximum number of approved commands before the session is
            force-concluded ``inconclusive`` (the budget backstop).
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (FR-17 Slice 5). Plan changes stay gated regardless.
        max_seconds: Wall-clock budget in seconds (free-launch only); ``None`` =
            no time bound.
        clock: Monotonic clock stamping the wall-clock budget's origin and used
            by the free-launch loop's budget check (injectable for tests).
    """
    sandbox.start()
    live = LiveSession(
        agent=agent,
        sandbox=sandbox,
        max_steps=max_steps,
        free_launch=free_launch,
        max_seconds=max_seconds,
        started_at=clock(),
    )
    registry.put(session_id, live)
    set_status(session, session_id, RetestSessionStatus.STARTING)
    deps = _make_deps(session, session_id, live)
    try:
        result = agent.run_sync(finding_prompt, deps=deps)
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)
    # In free-launch, drive the auto-approve loop from here; gated mode no-ops.
    _drive_auto(session, registry, session_id, clock=clock)


def _step_budget_exhausted(live: LiveSession) -> bool:
    """Count one more approved command and report whether the budget is now exceeded."""
    live.step_count += 1
    return live.step_count > live.max_steps


def _time_budget_exhausted(live: LiveSession, clock: Callable[[], float]) -> bool:
    """Report whether a free-launch session has exceeded its wall-clock budget.

    Checked only at step boundaries (the orchestrator holds control between agent
    turns, never mid-turn) and only in free-launch mode — in gated mode the
    elapsed time would include human think-time and trip falsely. ``None``
    ``max_seconds`` means no time bound.
    """
    if live.max_seconds is None:
        return False
    return clock() - live.started_at > live.max_seconds


def _give_up(session: Session, registry: SessionRegistry, session_id: int, reason: str) -> None:
    """Force-conclude a session ``inconclusive`` with a give-up ``reason`` and tear down.

    The shared exit for both budget backstops (step and wall-clock): record an
    ``inconclusive`` verdict citing the reason, mark the session ``given_up``,
    and stop the sandbox.
    """
    record_verdict(session, session_id, VerdictStatus.INCONCLUSIVE, reason)
    _mark_given_up(session, session_id)
    _teardown(registry, session_id)


def _consume_pending_call(live: LiveSession, command_id: str) -> str | None:
    """Atomically validate and consume ``live.pending_call_id`` under its lock.

    This is the compare-and-swap that makes two concurrent decisions on the
    same session safe (e.g. a double-click on Approve: the REST endpoint
    returns 202 immediately and re-enables the frontend button while the
    background ``run_decision`` task is still resuming the agent). Only the
    call that observes a matching ``pending_call_id`` consumes it (sets it to
    ``None``) and proceeds; every other caller — a duplicate, a decision on a
    stale/already-superseded command, or one referencing a mismatched
    ``cid`` — sees ``pending_call_id`` as already ``None`` (or non-matching)
    and no-ops. The lock is held only for this compare-and-swap, never across
    the subsequent LLM resume call.

    Args:
        live: The live session state to check/consume against.
        command_id: The ``cid`` the caller believes is pending (from the
            approve/reject URL).

    Returns:
        The consumed ``tool_call_id`` if it matched, or ``None`` if there was
        nothing pending or ``command_id`` didn't match it.
    """
    with live.lock:
        if live.pending_call_id is None or command_id != live.pending_call_id:
            return None
        call_id = live.pending_call_id
        live.pending_call_id = None
        return call_id


def _decision_event_kind(pending_kind: str, *, approved: bool) -> SessionEventKind:
    """Map the pending approval kind + decision to its transcript event kind."""
    if pending_kind == "plan":
        return SessionEventKind.PLAN_APPROVED if approved else SessionEventKind.PLAN_REJECTED
    return SessionEventKind.COMMAND_APPROVED if approved else SessionEventKind.COMMAND_REJECTED


def _resume_with_decision(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    live: LiveSession,
    call_id: str,
    *,
    approved: bool,
    reason: str,
) -> None:
    """Resume the agent run with the human decision on ``call_id`` (post-lock).

    Split out of :func:`apply_decision` so the lock-holding compare-and-swap
    stays a small, easily-audited critical section; everything here — the
    budget check, the LLM resume call — runs AFTER the lock has been released.
    Only *command* approvals count against the step budget; a plan change runs
    no command, so it is exempt and resumes in a transient ``thinking`` state.
    """
    is_command = live.pending_kind == "command"
    if approved and is_command and _step_budget_exhausted(live):
        _give_up(session, registry, session_id, "budget exhausted")
        return

    transient = RetestSessionStatus.RUNNING_COMMAND if is_command else RetestSessionStatus.THINKING
    set_status(session, session_id, transient)
    results = DeferredToolResults()
    if approved:
        # Approved: the run_command tool runs and drains observations into its
        # own result (see _make_deps / the agent tool).
        results.approvals[call_id] = ToolApproved()
    else:
        # Rejected: no tool runs, so fold any operator activity into the denial
        # message here — the agent still observes what the human did.
        results.approvals[call_id] = ToolDenied(reason + format_observations(live.drain()))
    deps = _make_deps(session, session_id, live)
    queued = live.drain_messages()
    user_prompt = "\n".join(queued) if queued else None
    try:
        result = live.agent.run_sync(
            user_prompt,
            deps=deps,
            message_history=live.messages,
            deferred_tool_results=results,
        )
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)


def _drive_auto(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Auto-approve successive command proposals while free-launch is on (FR-17 Slice 5).

    The free-launch loop. Iterative on purpose — one :func:`_resume_with_decision`
    call per pass, never recursing through :func:`apply_decision` — so a large
    ``max_steps`` cannot blow the stack. Each auto-approval goes through the same
    compare-and-swap (:func:`_consume_pending_call`) and step-budget check as a
    human approval, and is recorded as a ``command_approved`` event flagged
    ``{"auto": True}`` so the transcript stays honest about what a human vetted.

    The loop stops when: the session is torn down (concluded / gave up), the
    agent proposes a ``set_plan`` (``pending_kind != "command"`` — plan changes
    are **always** gated), free-launch is turned off, or a budget bound trips.
    In gated mode the first guard returns immediately, so callers can invoke this
    unconditionally.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session to drive.
        clock: Monotonic clock for the wall-clock budget check (injectable for
            tests); shares the origin stamped by :func:`start_and_step`.
    """
    while True:
        live = registry.get(session_id)
        if (
            live is None
            or not live.free_launch
            or live.pending_kind != "command"
            or live.pending_call_id is None
        ):
            return
        if _time_budget_exhausted(live, clock):
            _give_up(session, registry, session_id, "time budget exhausted")
            return
        call_id = _consume_pending_call(live, live.pending_call_id)
        if call_id is None:
            return  # a concurrent human decision took the pending command
        append_event(session, session_id, SessionEventKind.COMMAND_APPROVED, {"auto": True})
        _resume_with_decision(
            session, registry, session_id, live, call_id, approved=True, reason=""
        )


def apply_decision(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    *,
    approved: bool,
    reason: str = "",
    command_id: str,
) -> None:
    """Resume a paused run with a human decision on the pending command.

    Each *approval* counts against ``live.max_steps``; exceeding it force-
    concludes the session ``inconclusive`` and tears the sandbox down WITHOUT
    running the over-budget command (the budget backstop bounds an
    always-proposing agent). Rejections never run a command, so they never
    count against the budget.

    ``command_id`` must match the session's currently pending ``tool_call_id``
    (validated + consumed atomically under ``live.lock``, see
    ``_consume_pending_call``); a stale, duplicate, or mismatched decision is a
    silent no-op. This closes a double-approve race: without it, two
    concurrent decisions on the same command could both pass the guard, both
    resume the agent (running the approved command twice in the sandbox) and
    both append transcript events under colliding ``seq`` numbers.

    Args:
        session: Active DB session for this call — always freshly obtained by
            the caller, never held across separate orchestration calls.
        registry: The live-session registry.
        session_id: The retest session to resume.
        approved: Whether the pending command was approved.
        reason: Optional human-supplied reason, recorded and (on rejection)
            surfaced back to the model as the tool's denial message.
        command_id: The ``cid`` from the approve/reject URL; must match the
            session's pending ``tool_call_id`` or the call no-ops.
    """
    live = registry.get(session_id)
    if live is None:
        return
    call_id = _consume_pending_call(live, command_id)
    if call_id is None:
        return  # stale, duplicate, or mismatched decision: no-op

    kind = _decision_event_kind(live.pending_kind, approved=approved)
    append_event(session, session_id, kind, {"reason": reason} if reason else {})
    _resume_with_decision(
        session, registry, session_id, live, call_id, approved=approved, reason=reason
    )
    # In free-launch, a human decision that yields a new command proposal is then
    # auto-driven; in gated mode _drive_auto returns immediately (unchanged path).
    _drive_auto(session, registry, session_id)


def set_free_launch(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    enabled: bool,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Toggle free-launch on a live session (FR-17 Slice 5).

    Updates the persisted mode + the live flag, records a ``free_launch_changed``
    transcript event, and — when enabling with a command already pending —
    auto-approves it (and any that follow) via :func:`_drive_auto`. A no-op if
    the session is not live (already ended/concluded, or never started): there is
    nothing to steer once torn down, and the persisted mode is fixed at that point.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session to toggle.
        enabled: The new free-launch state.
        clock: Monotonic clock for the wall-clock budget check (injectable for
            tests); forwarded to :func:`_drive_auto`.
    """
    live = registry.get(session_id)
    if live is None:
        return
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    record.free_launch = enabled
    session.commit()
    live.free_launch = enabled
    append_event(session, session_id, SessionEventKind.FREE_LAUNCH_CHANGED, {"enabled": enabled})
    if enabled:
        _drive_auto(session, registry, session_id, clock=clock)


def _summarize_human_command(command: str, result: CommandResult) -> str:
    """Render a manual operator command + result as the note the agent will read."""
    return (
        f"The operator manually ran: {command}\n"
        f"exit_code={result.exit_code}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def submit_human_command(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    command: str,
    *,
    timeout: float = 30.0,
) -> None:
    """Run a manual operator command (`!`) in the live session's sandbox (FR-17 Slice 2).

    The human's own commands are **ungated** (single trusted user, ADR-0008) and
    run through the *same* discrete ``sandbox.exec`` the agent uses — no shared
    PTY (ADR-0026). The command + its result are recorded as a ``HUMAN_COMMAND``
    transcript event (so the terminal shows it) and buffered on the live session
    so the agent observes it on its next turn.

    A no-op if the session is not live (already ended/concluded, or never
    started) — there is no sandbox to run in once torn down.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (holds the sandbox + observation buffer).
        session_id: The retest session to run the command in.
        command: The exact shell command the operator submitted (without the `!`).
        timeout: Per-command timeout, matching the agent's command budget.
    """
    live = registry.get(session_id)
    if live is None:
        return
    result = live.sandbox.exec(command, timeout=timeout)
    append_event(
        session,
        session_id,
        SessionEventKind.HUMAN_COMMAND,
        {
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "elapsed_ms": result.elapsed_ms,
        },
    )
    live.observe(_summarize_human_command(command, result))


def submit_message(session: Session, registry: SessionRegistry, session_id: int, text: str) -> None:
    """Queue a free-text operator chat message for the agent (FR-17 Slice 4).

    Recorded as a ``HUMAN_MESSAGE`` transcript event (so the chat shows it and it
    replays) and buffered on the live session; delivered to the agent as a
    first-class user turn on the next approve/reject resume
    (:func:`_resume_with_decision`) — the pure-queue model (the agent is never
    idle, so a message can only land at the next turn boundary). Distinct from the
    `!` command path (:func:`submit_human_command`): a chat message is the
    operator's *voice*, not an observed command result.

    A no-op if the session is not live (already ended/concluded, or never
    started) — there is nothing to steer once torn down.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (holds the message buffer).
        session_id: The retest session to message.
        text: The exact operator message.
    """
    live = registry.get(session_id)
    if live is None:
        return
    append_event(session, session_id, SessionEventKind.HUMAN_MESSAGE, {"text": text})
    live.receive_message(text)


def end_session(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Operator-initiated end: tear down and mark ``ended`` (no-op if already terminal).

    Acquires ``live.lock`` around the teardown for consistency with
    ``apply_decision``'s registry-mutating critical section, even though
    ``end_session`` doesn't touch ``pending_call_id`` itself.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) in _TERMINAL:
        return
    set_status(session, session_id, RetestSessionStatus.ENDED)
    live = registry.get(session_id)
    if live is None:
        return
    with live.lock:
        _teardown(registry, session_id)


def _mark_given_up(session: Session, session_id: int) -> None:
    """Force status to ``given_up``, overriding the ``concluded`` set by ``record_verdict``."""
    record = session.get(RetestSessionRecord, session_id)
    if record is not None:
        record.status = RetestSessionStatus.GIVEN_UP.value
        session.commit()
        append_event(
            session,
            session_id,
            SessionEventKind.STATE_CHANGE,
            {"to": RetestSessionStatus.GIVEN_UP.value},
        )


def _fail(session: Session, registry: SessionRegistry, session_id: int, detail: str) -> None:
    """Record an ``error`` event, set status ``error``, and tear down (orchestration boundary)."""
    append_event(session, session_id, SessionEventKind.ERROR, {"detail": detail})
    set_status(session, session_id, RetestSessionStatus.ERROR)
    _teardown(registry, session_id)
