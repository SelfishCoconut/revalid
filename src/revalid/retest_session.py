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

from revalid.db import RetestSessionRecord, SessionEventRecord, VerdictRecord
from revalid.domain import (
    AgenticEvidence,
    RetestSessionStatus,
    SessionEventKind,
    VerdictStatus,
)
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
) -> RetestSessionRecord:
    """Insert a ``starting`` session row and return it.

    Args:
        session: Active DB session.
        finding_id: The finding identity (FR-16) this session retests.
        model: The resolved LLM model string driving the agent.
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (plan changes stay gated regardless). FR-17 Slice 5.
        max_steps: Step budget — approved commands before the session pauses for
            operator guidance (ADR-0034); the operator can raise it and continue.
    """
    record = RetestSessionRecord(
        finding_id=finding_id,
        status=RetestSessionStatus.STARTING.value,
        model=model,
        free_launch=free_launch,
        max_steps=max_steps,
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


# Cap a captured command's output in a verdict's evidence, mirroring the HTTP
# probe body cap (retest._BODY_EXCERPT_LIMIT): a chatty tool must not bloat a row.
_OUTPUT_EXCERPT_LIMIT = 16_384


def _last_command_output(session: Session, session_id: int) -> dict[str, Any] | None:
    """Return the session's most recent ``command_output`` payload, or ``None``."""
    rows = session.scalars(
        select(SessionEventRecord)
        .where(
            SessionEventRecord.session_id == session_id,
            SessionEventRecord.kind == SessionEventKind.COMMAND_OUTPUT.value,
        )
        .order_by(SessionEventRecord.seq)
    ).all()
    return dict(rows[-1].payload) if rows else None


def _build_agentic_evidence(session: Session, session_id: int, rationale: str) -> AgenticEvidence:
    """Assemble the agent's verdict proof: its rationale + the real last command output.

    The proof is the *actual* captured output of the decisive command (the last
    one the agent ran), not the model restating it — so it stays consistent with
    the transcript the FR-10 audit checks. Explanation-only when no command ran.
    """
    last = _last_command_output(session, session_id)
    if last is None:
        return AgenticEvidence(explanation=rationale)
    stdout = str(last.get("stdout", ""))
    stderr = str(last.get("stderr", ""))
    output = stdout if not stderr else f"{stdout}\n--- stderr ---\n{stderr}"
    exit_code = last.get("exit_code")
    return AgenticEvidence(
        explanation=rationale,
        command=str(last.get("command", "")),
        output=output[:_OUTPUT_EXCERPT_LIMIT],
        exit_code=exit_code if isinstance(exit_code, int) else None,
        elapsed_ms=float(last.get("elapsed_ms", 0.0)),
    )


def record_verdict(
    session: Session,
    session_id: int,
    status: VerdictStatus,
    rationale: str,
    *,
    actor: str = "agent",
    reason_code: str = "agentic_conclusion",
) -> None:
    """Persist a determination on the session row + its transcript events.

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
    polling the row directly — without it, a conclude would leave the UI showing
    the pre-verdict status forever. Both terminal producers route here: the agent
    concluding ``fixed``/``still_open`` (``actor="agent"``) and the operator
    manually concluding a paused session (``actor="operator"``, the only path that
    writes ``inconclusive`` under ADR-0034).
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
    # FR-09/Slice 6a: the conclusion becomes a queryable agentic verdict so a
    # session's outcome reaches the `verdicts` table, the FR-10 audit, and the
    # FR-12 export — the agent's own (actor="agent") with no human action, or the
    # operator's manual conclude (actor="operator").
    session.add(
        VerdictRecord.agentic(
            finding_id=record.finding_id,
            session_id=session_id,
            status=status,
            rationale=rationale,
            actor=actor,
            reason_code=reason_code,
            evidence=_build_agentic_evidence(session, session_id, rationale).model_dump(),
        )
    )
    session.commit()


def adjudicate_verdict(
    session: Session, session_id: int, status: VerdictStatus, rationale: str
) -> None:
    """Record a human adjudication of a concluded session's verdict (FR-17 Slice 6a).

    The human accepts or overrides the agent's conclusion. Either way this
    **appends** a superseding agentic verdict (``actor="operator"``) — the agent's
    record is never mutated (append-only; FR-10 intact) — plus a
    ``verdict_adjudicated`` transcript event, and updates the session row so its
    view shows the final call. Latest-per-finding (highest verdict id) is
    authoritative, so the operator record supersedes the agent's.

    A pure DB operation: the session is already terminal (torn down), so the live
    registry is never touched. A no-op if the session doesn't exist or has no
    agent verdict yet (nothing to adjudicate) — the guard also makes a premature
    or duplicate call safe.

    Args:
        session: Active DB session for this call.
        session_id: The concluded retest session being adjudicated.
        status: The human's verdict (may equal or differ from the agent's).
        rationale: The human's justification.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or record.verdict_status is None:
        return
    append_event(
        session,
        session_id,
        SessionEventKind.VERDICT_ADJUDICATED,
        {"status": status.value, "rationale": rationale},
    )
    session.add(
        VerdictRecord.agentic(
            finding_id=record.finding_id,
            session_id=session_id,
            status=status,
            rationale=rationale,
            actor="operator",
            reason_code="operator_adjudication",
        )
    )
    record.verdict_status = status.value
    record.verdict_rationale = rationale
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
    step_count: int = 0
    max_steps: int = 8
    #: Whether the agent's commands auto-run without a per-command human approval
    #: (FR-17 Slice 5). Plan changes stay gated regardless. Toggled live by
    #: ``set_free_launch``; the free-launch loop lives in ``_drive_auto``.
    free_launch: bool = False
    #: Paused for operator guidance (ADR-0034): a step budget was reached or the
    #: agent handed back. Set by ``_pause_for_guidance``, cleared by
    #: ``continue_session``; the free-launch loop stops while it is ``True``.
    awaiting_guidance: bool = False
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

    #: The current goal (FR-17 6b-ii) queued by an operator edit since the agent's
    #: last turn, delivered as a user turn on the next resume (like human_messages).
    #: ``None`` means no pending change. Guarded by ``lock``.
    pending_goal: list[str] | None = None

    def set_pending_goal(self, steps: list[str]) -> None:
        """Queue a goal change for the agent's next turn (thread-safe)."""
        with self.lock:
            self.pending_goal = list(steps)

    def drain_goal(self) -> list[str] | None:
        """Atomically return and clear the queued goal change (thread-safe)."""
        with self.lock:
            drained = self.pending_goal
            self.pending_goal = None
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

    def emit_message(message: str) -> None:
        append_event(session, session_id, SessionEventKind.AGENT_MESSAGE, {"text": message})

    return RetestSessionDeps(
        sandbox=live.sandbox,
        emit_output=emit,
        drain_observations=live.drain,
        emit_message=emit_message,
    )


def _emit_proposal(session: Session, session_id: int, live: LiveSession, call: Any) -> None:
    """Record the agent's proposed command and mark it pending.

    The gate now only ever carries a ``run_command`` (the agent's ``set_plan``
    was removed in FR-17 6b-ii): a proposal is always a command awaiting a
    decision. The caller sets the status — ``awaiting_command`` normally, or
    ``needs_guidance`` when the step budget is spent (:func:`_dispatch_output`).
    """
    args = call.args_as_dict()
    live.pending_call_id = call.tool_call_id
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
        _emit_proposal(session, session_id, live, output.approvals[0])
        # Gate on the step budget: if it is spent, pause and ask the operator
        # instead of soliciting another approval. The command stays pending; its
        # gate re-opens once the operator raises the budget (continue_session).
        if live.step_count >= live.max_steps:
            _pause_for_guidance(
                session, registry, session_id, f"Reached the {live.max_steps}-command budget."
            )
        else:
            set_status(session, session_id, RetestSessionStatus.AWAITING_COMMAND)
    elif isinstance(output, ConcludeOutput):
        if output.status is VerdictStatus.INCONCLUSIVE:
            # The agent hands back rather than terminating: it has exhausted the
            # options it can think of and asks the operator to steer or conclude
            # (ADR-0034). No verdict is written; the sandbox stays alive.
            _pause_for_guidance(session, registry, session_id, output.rationale)
        else:
            record_verdict(session, session_id, output.status, output.rationale)
            _teardown(registry, session_id)


def _pause_for_guidance(
    session: Session, registry: SessionRegistry, session_id: int, reason: str
) -> None:
    """Pause a session for operator guidance (ADR-0034): no verdict, sandbox kept alive.

    Records a ``needs_guidance`` event carrying the human-readable ``reason`` and
    moves the session to the non-terminal ``NEEDS_GUIDANCE`` state, so the SPA
    shows a pause banner (Keep going / Conclude) while the operator can still run
    commands and steer via chat. The live session is retained (its sandbox is not
    torn down); the free-launch loop halts on ``awaiting_guidance``.
    """
    append_event(session, session_id, SessionEventKind.NEEDS_GUIDANCE, {"reason": reason})
    set_status(session, session_id, RetestSessionStatus.NEEDS_GUIDANCE)
    live = registry.get(session_id)
    if live is not None:
        live.awaiting_guidance = True


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
        max_steps: Approved commands before the session pauses for operator
            guidance (ADR-0034); the operator can raise it and continue.
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (FR-17 Slice 5). Plan changes stay gated regardless.
    """
    sandbox.start()
    live = LiveSession(agent=agent, sandbox=sandbox, max_steps=max_steps, free_launch=free_launch)
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
    _drive_auto(session, registry, session_id)


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


def _decision_event_kind(*, approved: bool) -> SessionEventKind:
    """Map a command decision to its transcript event kind."""
    return SessionEventKind.COMMAND_APPROVED if approved else SessionEventKind.COMMAND_REJECTED


def _resume_prompt(goal: list[str] | None, messages: list[str]) -> str | None:
    """Combine a queued goal change + queued operator messages into one user turn (6b-ii).

    Both are delivered on the next agent resume; ``None`` when neither is pending.
    """
    parts: list[str] = []
    if goal:
        steps = "\n".join(f"- {s}" for s in goal)
        parts.append(f"The operator set the goal to:\n{steps}")
    parts.extend(messages)
    return "\n\n".join(parts) if parts else None


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
    stays a small, easily-audited critical section; the LLM resume call runs
    AFTER the lock has been released. Every pending call is a command now (the
    agent's ``set_plan`` was removed in 6b-ii), so each *approval* counts one step
    against the budget; the budget is enforced when the agent proposes its next
    command (:func:`_dispatch_output`), where a spent budget pauses for guidance.
    """
    set_status(session, session_id, RetestSessionStatus.RUNNING_COMMAND)
    results = DeferredToolResults()
    if approved:
        # Approved: count the step, then run. The run_command tool runs and drains
        # observations into its own result (see _make_deps / the agent tool).
        live.step_count += 1
        results.approvals[call_id] = ToolApproved()
    else:
        # Rejected: no tool runs, so fold any operator activity into the denial
        # message here — the agent still observes what the human did.
        results.approvals[call_id] = ToolDenied(reason + format_observations(live.drain()))
    deps = _make_deps(session, session_id, live)
    user_prompt = _resume_prompt(live.drain_goal(), live.drain_messages())
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


def _drive_auto(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Auto-approve successive command proposals while free-launch is on (FR-17 Slice 5).

    The free-launch loop. Iterative on purpose — one :func:`_resume_with_decision`
    call per pass, never recursing through :func:`apply_decision` — so a large
    ``max_steps`` cannot blow the stack. Each auto-approval goes through the same
    compare-and-swap (:func:`_consume_pending_call`) as a human approval, and is
    recorded as a ``command_approved`` event flagged ``{"auto": True}`` so the
    transcript stays honest about what a human vetted.

    The loop stops when: the session is torn down (concluded / ended), free-launch
    is turned off, or the session has paused for guidance (``awaiting_guidance`` —
    a spent step budget, ADR-0034; the pending command is held, not auto-run). In
    gated mode the first guard returns immediately, so callers can invoke this
    unconditionally.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session to drive.
    """
    while True:
        live = registry.get(session_id)
        if (
            live is None
            or not live.free_launch
            or live.pending_call_id is None
            or live.awaiting_guidance
        ):
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

    Each *approval* counts one step against ``live.max_steps``; when the agent
    then proposes its next command with the budget spent, the session pauses for
    operator guidance rather than force-concluding (:func:`_dispatch_output`,
    ADR-0034). Rejections never run a command, so they never count.

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

    kind = _decision_event_kind(approved=approved)
    append_event(session, session_id, kind, {"reason": reason} if reason else {})
    _resume_with_decision(
        session, registry, session_id, live, call_id, approved=approved, reason=reason
    )
    # In free-launch, a human decision that yields a new command proposal is then
    # auto-driven; in gated mode _drive_auto returns immediately (unchanged path).
    _drive_auto(session, registry, session_id)


def set_free_launch(
    session: Session, registry: SessionRegistry, session_id: int, enabled: bool
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
        _drive_auto(session, registry, session_id)


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


def set_goal(
    session: Session, registry: SessionRegistry, session_id: int, steps: list[str]
) -> None:
    """Set the user-owned goal on a non-terminal session (FR-17 6b-ii).

    Appends a ``plan_updated`` transcript event so the "Current goal" panel reflects
    the edit (and it replays); when a live agent is attached, the goal is also queued
    and delivered to it as a first-class user turn on the next approve/reject
    (:func:`_resume_with_decision`) — pure-queue, never interrupting a run.

    The event is emitted for **any** non-terminal session, live or not: the live
    orchestration state is process-local, so a session that outlives a backend
    restart is non-terminal yet has no live agent. Gating the panel update on
    liveness (the prior behaviour) made such an edit silently vanish — the endpoint
    still returned 202 but the panel kept the old steps. Emitting regardless keeps
    the edit visible; queuing stays conditional on a live agent existing to receive
    it. A no-op only when the session is unknown or already terminal.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (holds the goal buffer).
        session_id: The retest session whose goal to set.
        steps: The operator's goal steps (replaces the whole goal).
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or is_terminal(RetestSessionStatus(record.status)):
        return
    append_event(session, session_id, SessionEventKind.PLAN_UPDATED, {"steps": list(steps)})
    live = registry.get(session_id)
    if live is not None:
        live.set_pending_goal(steps)


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


def _resume_run(
    session: Session, registry: SessionRegistry, session_id: int, live: LiveSession
) -> None:
    """Re-run the agent to continue after an exhausted-options pause (ADR-0034).

    Used by :func:`continue_session` when no command is held pending — the agent
    ended its turn handing back to the operator, so we resume it with any queued
    goal/chat steering (or a plain nudge) and dispatch its next output.
    """
    set_status(session, session_id, RetestSessionStatus.RUNNING_COMMAND)
    deps = _make_deps(session, session_id, live)
    user_prompt = _resume_prompt(live.drain_goal(), live.drain_messages()) or (
        "Continue the retest toward a determination."
    )
    try:
        result = live.agent.run_sync(user_prompt, deps=deps, message_history=live.messages)
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)
    _drive_auto(session, registry, session_id)


def continue_session(
    session: Session, registry: SessionRegistry, session_id: int, *, extra_steps: int = 8
) -> None:
    """Resume a paused session, raising the step budget — ADR-0034 "Keep going".

    A no-op unless the session is paused in ``needs_guidance`` with a live agent (a
    paused session that outlived a backend restart has no sandbox to resume — the
    operator restarts it instead). Raises ``max_steps`` by ``extra_steps``, clears
    the pause, and continues: a command held at the budget re-opens its approve
    gate (auto-run in free-launch); an exhausted-options pause re-runs the agent,
    folding in any queued goal/chat guidance.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The paused retest session to resume.
        extra_steps: How many more approved commands to allow before the next pause.
    """
    record = session.get(RetestSessionRecord, session_id)
    if (
        record is None
        or RetestSessionStatus(record.status) is not RetestSessionStatus.NEEDS_GUIDANCE
    ):
        return
    live = registry.get(session_id)
    if live is None:
        return
    live.max_steps += extra_steps
    record.max_steps = live.max_steps
    session.commit()
    live.awaiting_guidance = False
    if live.pending_call_id is not None:
        # A command was held at the budget: re-open its gate (auto-run in free-launch).
        set_status(session, session_id, RetestSessionStatus.AWAITING_COMMAND)
        _drive_auto(session, registry, session_id)
    else:
        _resume_run(session, registry, session_id, live)


def conclude_session(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    status: VerdictStatus,
    rationale: str,
) -> None:
    """Operator manually concludes a session with a determination — ADR-0034.

    The operator's own verdict for a paused (or otherwise live) session: writes it
    (``actor="operator"``, the only path that can record ``inconclusive``) and
    tears down the sandbox. A no-op if the session is already terminal. Works even
    on an orphaned paused session (the verdict is recorded; the teardown no-ops).

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The session to conclude.
        status: The operator's determination.
        rationale: The operator's justification.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) in _TERMINAL:
        return
    record_verdict(
        session,
        session_id,
        status,
        rationale,
        actor="operator",
        reason_code="operator_conclusion",
    )
    _teardown(registry, session_id)


def _fail(session: Session, registry: SessionRegistry, session_id: int, detail: str) -> None:
    """Record an ``error`` event, set status ``error``, and tear down (orchestration boundary)."""
    append_event(session, session_id, SessionEventKind.ERROR, {"detail": detail})
    set_status(session, session_id, RetestSessionStatus.ERROR)
    _teardown(registry, session_id)
