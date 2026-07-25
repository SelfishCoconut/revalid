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
resume it. When the agent exhausts the options it can think of, it hands back
to the operator (``awaiting_operator``, ADR-0034/0042) rather than running forever.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import (
    AgentRunResult,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import (
    ModelMessage,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revalid.db import RetestSessionRecord, SessionEventRecord, VerdictRecord
from revalid.deltas import DELTAS, DeltaChannel
from revalid.domain import (
    AgenticEvidence,
    RetestSessionStatus,
    SessionEventKind,
    VerdictStatus,
)
from revalid.retest_agent import (
    DEFAULT_COMMAND_TIMEOUT,
    MAX_COMMAND_TIMEOUT,
    AwaitOperator,
    ConcludeOutput,
    RetestAgent,
    RetestOutput,
    RetestSessionDeps,
    clamp_timeout,
    format_observations,
)
from revalid.sandbox import CommandResult, Sandbox
from revalid.scope import scope_hosts

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
    deferred: bool = False,
) -> RetestSessionRecord:
    """Insert a session row and return it.

    Args:
        session: Active DB session.
        finding_id: The finding identity (FR-16) this session retests.
        model: The resolved LLM model string driving the agent.
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (plan changes stay gated regardless). FR-17 Slice 5.
        deferred: When ``True``, open the session ``idle`` (created but not started)
            so it waits for an operator ``Start`` instead of auto-running — the
            Restart path (issue #150). Default ``False`` opens it ``starting``.
    """
    status = RetestSessionStatus.IDLE if deferred else RetestSessionStatus.WORKING
    record = RetestSessionRecord(
        finding_id=finding_id,
        status=status.value,
        model=model,
        free_launch=free_launch,
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


def _latest_payload(events: list[dict[str, Any]], kind: SessionEventKind) -> dict[str, Any] | None:
    """Return the payload of the most recent event of ``kind``, or ``None``."""
    for event in reversed(events):
        if event["kind"] == kind.value:
            payload: dict[str, Any] = event["payload"]
            return payload
    return None


def session_goal(session: Session, session_id: int) -> tuple[str, ...]:
    """Return the session's current goal steps (latest ``plan_updated``), or empty.

    Reads the goal back from the transcript so a deferred (``idle``) session's
    Start (issue #150) reconstructs the goal recorded at create time, without any
    in-memory carry that a backend restart would lose.
    """
    payload = _latest_payload(
        load_events_after(session, session_id, 0), SessionEventKind.PLAN_UPDATED
    )
    steps = payload.get("steps") if payload else None
    return tuple(str(s) for s in steps) if isinstance(steps, list) else ()


def session_scope(session: Session, session_id: int) -> tuple[str, ...]:
    """Return the session's retest scope (the launch ``target_set`` endpoints), or empty."""
    payload = _latest_payload(
        load_events_after(session, session_id, 0), SessionEventKind.TARGET_SET
    )
    endpoints = payload.get("endpoints") if payload else None
    return tuple(str(e) for e in endpoints) if isinstance(endpoints, list) else ()


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


def reopen_session(session: Session, session_id: int) -> None:
    """Reopen a concluded session so the operator can keep testing (issue #214).

    Withdraws the recorded verdict and returns the session to ``idle``, whose wake
    path re-provisions the sandbox and continues from the transcript (goal + scope
    reconstructed there). The verdict is **kept in the transcript** — the ``VERDICT``
    event plus a new ``VERDICT_CANCELLED`` event — which is the append-only audit
    for an agentic session (ADR-0025); its row in the queryable ``verdicts``
    projection is removed because a withdrawn verdict is no longer a current
    determination, so the finding stops showing an outcome the operator retracted.

    A pure DB operation (the session is already torn down, so the live registry is
    never touched). A no-op unless the session is ``concluded`` — so a premature or
    duplicate call is safe.

    Args:
        session: Active DB session for this call.
        session_id: The concluded retest session to reopen.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) is not RetestSessionStatus.CONCLUDED:
        return
    append_event(
        session,
        session_id,
        SessionEventKind.VERDICT_CANCELLED,
        {"status": record.verdict_status},
    )
    append_event(
        session,
        session_id,
        SessionEventKind.STATE_CHANGE,
        {"to": RetestSessionStatus.IDLE.value},
    )
    for verdict in session.scalars(
        select(VerdictRecord).where(VerdictRecord.session_id == session_id)
    ):
        session.delete(verdict)
    record.status = RetestSessionStatus.IDLE.value
    record.verdict_status = None
    record.verdict_rationale = None
    record.ended_at = None
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
        lock: Guards the compare-and-swap on ``pending_call_id`` in
            ``apply_decision`` so two concurrent decisions (e.g. a double-click
            on Approve before the REST 202 re-enables the button) can't both
            observe the same pending call and both resume the agent run.
    """

    agent: RetestAgent
    sandbox: Sandbox
    messages: list[ModelMessage] = field(default_factory=list)
    pending_call_id: str | None = None
    #: Whether the agent's commands auto-run without a per-command human approval
    #: (FR-17 Slice 5) — the one deliberate relaxation of the gate; the egress lock
    #: is unaffected. Toggled live by ``set_free_launch``; the loop lives in
    #: ``_advance``, which also delivers queued operator messages (ADR-0042).
    free_launch: bool = False
    #: The agent handed control back (ADR-0034/0042): a reply, a guided report, a
    #: recommendation, or "I'm stuck". Set by ``_await_operator``, cleared by
    #: ``continue_session``; the free-launch loop stops while it is ``True``.
    awaiting_guidance: bool = False
    #: The operator pressed Stop (issue #150): a cooperative pause. Set by
    #: ``stop_session``, cleared by ``resume_session``. An in-flight step that
    #: finishes while this is ``True`` parks the session in ``stopped`` instead of
    #: advancing, and the free-launch loop halts — the sandbox is kept alive.
    stopped: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Manual operator commands run since the agent's last turn (from the console
    #: terminal's ``operator$`` prompt), buffered here and surfaced to the agent on
    #: its next turn so it observes what the human did (FR-17 Slice 2, ADR-0026).
    #: Guarded by ``lock`` — appended by the human-
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

    def has_queued_messages(self) -> bool:
        """Whether operator chat messages are waiting for the next turn (thread-safe).

        Peeked (not drained) by :func:`_advance` to decide whether to deliver them at
        a turn boundary — the actual drain happens inside the resume it then runs.
        """
        with self.lock:
            return bool(self.human_messages)

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

    #: The event loop + task driving the in-flight agent turn, when one is running
    #: (issue #204). Held so another thread — the ``restart-model`` endpoint or a
    #: teardown — can cancel a wedged turn cross-thread via
    #: ``loop.call_soon_threadsafe(task.cancel)``. Both ``None`` between turns.
    #: Guarded by ``lock``; the run thread attaches on start, detaches on completion.
    _run_loop: asyncio.AbstractEventLoop | None = None
    _run_task: asyncio.Task[Any] | None = None
    #: Set when the operator asked to abort-and-*retry* the in-flight turn (unstick,
    #: issue #204). The run thread, on catching the cancellation, consumes this and
    #: re-runs the same turn instead of failing. A plain cancel (teardown) leaves it
    #: ``False`` so the cancellation settles the session. Guarded by ``lock``.
    abort_retry: bool = False

    def attach_run(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task[Any]) -> None:
        """Register the loop + task of the turn now starting (thread-safe).

        Resets ``abort_retry`` so a stale flag from a prior turn never bleeds into
        this one — each turn starts with clean cancellation state.
        """
        with self.lock:
            self._run_loop = loop
            self._run_task = task
            self.abort_retry = False

    def detach_run(self) -> None:
        """Clear the in-flight-turn handle once the turn completes (thread-safe)."""
        with self.lock:
            self._run_loop = None
            self._run_task = None

    def request_restart(self) -> bool:
        """Abort the in-flight turn and mark it to be re-run — unstick (issue #204).

        Cancels the run's asyncio task from this (foreign) thread and flags the run
        thread to retry the same turn rather than fail. Returns whether a turn was
        actually in flight to abort (``False`` = nothing to unstick).
        """
        with self.lock:
            loop, task = self._run_loop, self._run_task
            if loop is None or task is None:
                return False
            self.abort_retry = True
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # the loop is already closing — the turn is ending anyway
            with self.lock:
                self.abort_retry = False
            return False
        return True

    def request_cancel(self) -> bool:
        """Abort the in-flight turn WITHOUT a retry — teardown (issues #204/#205).

        Like :meth:`request_restart` but leaves ``abort_retry`` ``False`` so the
        cancellation propagates out of the run thread (which then settles the
        already-terminal session) instead of re-running. Used when ending or
        deleting a session so a wedged turn's thread does not linger on a hung call.
        """
        with self.lock:
            loop, task = self._run_loop, self._run_task
        if loop is None or task is None:
            return False
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # the loop is already closing — nothing to cancel
            return False
        return True

    def consume_restart(self) -> bool:
        """Return + clear whether the aborted turn should be re-run (thread-safe)."""
        with self.lock:
            requested = self.abort_retry
            self.abort_retry = False
            return requested


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
        # Selects the agent's persona for this turn (ADR-0040): read live so a
        # mid-session Auto-run toggle switches guided ↔ autonomous next turn.
        free_launch=live.free_launch,
    )


def _emit_proposal(session: Session, session_id: int, live: LiveSession, call: Any) -> None:
    """Record the agent's proposed command and mark it pending.

    The gate now only ever carries a ``run_command`` (the agent's ``set_plan``
    was removed in FR-17 6b-ii): a proposal is always a command awaiting a
    decision. The caller then sets the status to ``awaiting_command``
    (:func:`_dispatch_output`).
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
            # The agent-chosen per-command cap (FR-17): surfaced at the gate so the
            # operator sees how long the command may run before approving. Absent
            # when the model omits it — the tool then applies its own default.
            "timeout_seconds": clamp_timeout(
                int(args.get("timeout_seconds", DEFAULT_COMMAND_TIMEOUT))
            ),
            "tool_call_id": call.tool_call_id,
        },
    )


def _suggestion_reason(call: Any) -> str:
    """Frame a guided-mode command the agent proposed post-run as an advisory suggestion.

    In guided mode the agent's own next proposal is never opened as a gate
    (ADR-0040); it is surfaced here as text in the hand-back so the operator can
    act on it, ask for something else, or conclude — the operator drives, the
    agent advises.
    """
    args = call.args_as_dict()
    command = str(args.get("command", "")).strip()
    rationale = str(args.get("rationale", "")).strip()
    lead = (
        f"I ran that. Next I'd suggest:\n`{command}`"
        if command
        else "Ready for your next instruction."
    )
    if rationale:
        lead += f"\n({rationale})"
    return f"{lead}\n\nTell me to go ahead, point me elsewhere, or conclude the retest."


def _recommendation_reason(output: ConcludeOutput) -> str:
    """Frame a guided-mode determination as a recommendation, not a ruling (ADR-0040).

    The agent never records a terminal verdict while guided: its ``fixed``/
    ``still_open`` is surfaced as advice for the operator to confirm (Conclude) or
    overrule by steering further.
    """
    label = output.status.value.replace("_", " ")
    return (
        f"Based on that, I think this finding is {label}: {output.rationale}\n\n"
        "Conclude to record this, adjust it, or keep steering me."
    )


def _dispatch_output(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    result: AgentRunResult[RetestOutput],
    *,
    after_command: bool = False,
) -> None:
    """Persist the outcome of one agent step: a proposed command or a verdict.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (to update transcript/pending state).
        session_id: The retest session this step belongs to.
        result: The run result just produced by ``agent.run_sync``.
        after_command: ``True`` when this step *ran* a command (an approved
            resume), which in guided mode is the one-action boundary: the session
            hands back rather than chaining the agent's next proposal (ADR-0040).
    """
    live = registry.get(session_id)
    if live is None:
        return
    live.messages = result.all_messages()
    output = result.output
    guided = not live.free_launch
    if isinstance(output, DeferredToolRequests) and output.approvals:
        proposal = output.approvals[0]
        if guided and after_command:
            # Guided one-action-then-park (ADR-0040): the operator's command has
            # run, so do NOT chain into the next gate. Drop the fresh proposal —
            # trimming the unresolved approval call from the history so a later
            # continue resumes from a clean state — and hand back, surfacing the
            # proposed command as an advisory suggestion rather than a demand.
            live.messages = result.all_messages()[:-1]
            _await_operator(session, registry, session_id, _suggestion_reason(proposal))
            return
        _emit_proposal(session, session_id, live, proposal)
        # If the operator pressed Stop while this step was thinking (issue #150),
        # hold the freshly proposed command and park in `stopped` rather than
        # opening the gate; Resume re-opens it. The pending call is retained.
        park = RetestSessionStatus.STOPPED if live.stopped else RetestSessionStatus.AWAITING_COMMAND
        set_status(session, session_id, park)
    elif isinstance(output, AwaitOperator):
        # The agent replied conversationally and handed back (issue #204): a
        # greeting, a small-talk answer, an acknowledgement. Park lightly and wait.
        _await_operator(session, registry, session_id, output.message)
    elif isinstance(output, ConcludeOutput):
        if output.status is VerdictStatus.INCONCLUSIVE:
            # The agent hands back rather than terminating: it has exhausted the
            # options it can think of and asks the operator to steer or conclude
            # (ADR-0034). No verdict is written; the sandbox stays alive.
            _await_operator(session, registry, session_id, output.rationale)
        elif guided:
            # Guided mode never self-concludes (ADR-0040): the agent's `fixed`/
            # `still_open` is a *recommendation*, surfaced for the operator to
            # confirm (Conclude) — only the operator records a terminal verdict.
            _await_operator(session, registry, session_id, _recommendation_reason(output))
        else:
            record_verdict(session, session_id, output.status, output.rationale)
            _teardown(registry, session_id)


def _await_operator(
    session: Session, registry: SessionRegistry, session_id: int, message: str
) -> None:
    """Park a session after the agent handed control back (ADR-0034/0039/0042).

    The single hand-back path (ADR-0042 folded ``needs_guidance`` into here): the
    agent answered conversationally, made a guided one-action report with a suggested
    next step, recommended a verdict for the operator to confirm, or said it has
    exhausted its options. ``message`` is surfaced as an ordinary ``agent_message``
    and the session moves to the non-terminal ``AWAITING_OPERATOR`` state — the
    sandbox stays alive, the free-launch loop halts on ``awaiting_guidance`` (reused
    as the generic "handed back" flag), and the operator's next message resumes it
    (:func:`continue_session`).
    """
    append_event(session, session_id, SessionEventKind.AGENT_MESSAGE, {"text": message})
    set_status(session, session_id, RetestSessionStatus.AWAITING_OPERATOR)
    live = registry.get(session_id)
    if live is not None:
        live.awaiting_guidance = True


def _mark_delivered(session: Session, session_id: int, messages: list[str]) -> None:
    """Record that queued operator messages reached the agent this turn (issue #204).

    Emitted at each turn boundary that drains and delivers queued chat messages, so
    the console can stop flagging a delivered message as still "queued". A no-op
    when nothing was queued (the common case).
    """
    if messages:
        append_event(session, session_id, SessionEventKind.MESSAGES_DELIVERED, {})


def _teardown(registry: SessionRegistry, session_id: int) -> None:
    """Stop the sandbox (if live) and drop the session from the registry."""
    live = registry.get(session_id)
    if live is not None:
        live.sandbox.stop()
        registry.drop(session_id)


class _TurnAbortedError(Exception):
    """An in-flight turn was cancelled for teardown, not to retry (issue #204).

    Raised out of :func:`run_agent_step` when a session's turn is cancelled via
    :meth:`LiveSession.request_cancel` (end/delete). It is a plain ``Exception``
    (not ``asyncio.CancelledError``, which is a ``BaseException`` the step sites'
    ``except Exception`` would miss) so the orchestration boundary catches it and
    routes to :func:`_fail`, which no-ops on the already-terminal session.
    """


def _run_cancellable_turn(
    drive: Callable[[], Coroutine[Any, Any, AgentRunResult[RetestOutput]]],
    *,
    session_id: int,
    channel: DeltaChannel,
    live: LiveSession | None,
) -> AgentRunResult[RetestOutput]:
    """Run one turn on a fresh event loop, re-running it on an operator unstick (#204).

    The loop is built by hand (not :func:`asyncio.run`) so ``live`` can hold its
    handle and another thread can cancel a wedged turn cross-thread. A cancel that
    was an operator *unstick* (:meth:`LiveSession.request_restart`) re-runs ``drive``
    from the top; a cancel for teardown re-raises as :class:`_TurnAbortedError`.
    """
    while True:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            task = loop.create_task(drive())
            if live is not None:
                live.attach_run(loop, task)
            return loop.run_until_complete(task)
        except asyncio.CancelledError:
            if live is not None and live.consume_restart():
                continue  # operator unstick: re-run the same turn on a fresh loop
            raise _TurnAbortedError("agent turn cancelled for teardown") from None
        finally:
            if live is not None:
                live.detach_run()
            # The turn is over: whatever it was thinking is superseded by the
            # transcript events it just produced, so the console stops showing it.
            channel.clear(session_id)
            asyncio.set_event_loop(None)
            loop.close()


def run_agent_step(
    agent: RetestAgent,
    user_prompt: str | None,
    *,
    session_id: int,
    deps: RetestSessionDeps,
    message_history: list[ModelMessage] | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
    channel: DeltaChannel = DELTAS,
    live: LiveSession | None = None,
) -> AgentRunResult[RetestOutput]:
    """Run one agent turn, streaming its live tokens to the console (issue #140).

    Replaces ``agent.run_sync`` at every step site. The turn's *result* is
    unchanged — same output union, same message history, same deferred-tool
    handling — so the orchestrator's state machine is untouched; the only
    addition is that tokens are published to ``channel`` as they arrive.

    **What actually streams.** This agent's output is structured
    (``ConcludeOutput``) or a gated tool request, and its commands and prose
    reach the operator as *tool arguments*, which the model emits whole rather
    than incrementally. What it does stream token-by-token is its **reasoning**:
    measured against a live ``ollama:qwen3:14b``, one turn produced 746 thinking
    deltas and zero text or tool-argument deltas. So the reasoning is what the
    console shows while a turn is in flight — which is exactly the stretch the
    operator previously spent watching a motionless spinner.

    Text deltas are forwarded too, for models that narrate in plain parts rather
    than a thinking part. Tool-argument deltas are deliberately **not**: they
    arrive as partial JSON (``{"rationale": "I will che``), and rendering that
    would show the operator half-escaped syntax rather than a sentence.

    Runs the async stream on its own event loop via :func:`_run_cancellable_turn`,
    which also carries the issue #204 cancel/retry: an operator *unstick*
    (:meth:`LiveSession.request_restart`) re-runs the same turn from the top, while
    a cancel for teardown surfaces as :class:`_TurnAbortedError`. Step sites are
    already background/worker threads with no loop running, and the orchestrator
    around them is synchronous — converting the whole state machine to async would
    be a far larger change for no behavioural gain here.

    Args:
        agent: The retest agent to run.
        user_prompt: The turn's prompt (``None`` when resuming from a tool result).
        session_id: The session whose console receives the tokens.
        deps: The tool dependencies for this turn.
        message_history: Prior turns, when continuing a run.
        deferred_tool_results: Approvals/denials resuming a gated call.
        channel: The live-token channel (injectable for tests).
        live: The live session, when its turn should be cancellable (issue #204).
            ``None`` (agent-unit tests) runs a plain, non-cancellable turn.

    Returns:
        The completed run result, exactly as ``run_sync`` would have returned it.

    Raises:
        RuntimeError: If the stream ends without producing a run result, which
            would otherwise surface as a confusing ``None`` far from here.
        _TurnAbortedError: If the turn was cancelled for teardown (not to retry).
    """

    async def drive() -> AgentRunResult[RetestOutput]:
        result: AgentRunResult[RetestOutput] | None = None
        async with agent.run_stream_events(
            user_prompt,
            deps=deps,
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
        ) as events:
            async for event in events:
                if isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, ThinkingPartDelta | TextPartDelta
                ):
                    channel.publish(session_id, event.delta.content_delta or "")
                elif isinstance(event, AgentRunResultEvent):
                    result = event.result
        if result is None:  # pragma: no cover - the library always ends with a result
            raise RuntimeError("agent stream ended without a result")
        return result

    return _run_cancellable_turn(drive, session_id=session_id, channel=channel, live=live)


def start_and_step(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    agent: RetestAgent,
    sandbox: Sandbox,
    finding_prompt: str,
    *,
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
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (FR-17 Slice 5). Plan changes stay gated regardless.
    """
    # Provision against the session's scope (ADR-0041): the launch `target_set`
    # endpoints parsed to their hosts. Lab scope keeps the unchanged internal
    # network; an online host provisions the L3 egress gateway (ADR-0045).
    sandbox.start(scope_hosts(session_scope(session, session_id)))
    live = LiveSession(agent=agent, sandbox=sandbox, free_launch=free_launch)
    registry.put(session_id, live)
    set_status(session, session_id, RetestSessionStatus.WORKING)
    deps = _make_deps(session, session_id, live)
    try:
        result = run_agent_step(agent, finding_prompt, session_id=session_id, deps=deps, live=live)
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)
    # Deliver any queued message and, in free-launch, auto-approve; else parks.
    _advance(session, registry, session_id)


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
    agent's ``set_plan`` was removed in 6b-ii), so a resume either runs the
    approved command or folds a rejection into the tool's denial message.
    """
    set_status(session, session_id, RetestSessionStatus.WORKING)
    results = DeferredToolResults()
    if approved:
        # Approved: the run_command tool runs and drains observations into its own
        # result (see _make_deps / the agent tool).
        results.approvals[call_id] = ToolApproved()
    else:
        # Rejected: no tool runs, so fold any operator activity into the denial
        # message here — the agent still observes what the human did.
        results.approvals[call_id] = ToolDenied(reason + format_observations(live.drain()))
    deps = _make_deps(session, session_id, live)
    goal, messages = live.drain_goal(), live.drain_messages()
    _mark_delivered(session, session_id, messages)
    user_prompt = _resume_prompt(goal, messages)
    try:
        result = run_agent_step(
            live.agent,
            user_prompt,
            session_id=session_id,
            deps=deps,
            message_history=live.messages,
            deferred_tool_results=results,
            live=live,
        )
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    # An approval *ran a command*: in guided mode that is the one-action boundary,
    # so the session hands back instead of chaining the agent's next proposal
    # (ADR-0040). A rejection ran nothing, so its follow-up proposal gates as usual.
    _dispatch_output(session, registry, session_id, result, after_command=approved)


#: Recorded on the withdrawn command when an operator message pre-empts the approval
#: gate — Claude Code's "type at the permission prompt" (a message steers instead of
#: approving). The agent sees the command was not run and reads the message.
_STEER_REASON = "Set aside — the operator sent a message instead of approving."


def _steer_pending_command(
    session: Session, registry: SessionRegistry, session_id: int, live: LiveSession
) -> bool:
    """Withdraw the pending command and resume with the queued operator message(s).

    The gate case of the message-routing rule (ADR-0042): a message while a command
    awaits approval withdraws it (records a ``command_rejected``) and re-runs the
    agent with the message delivered as a first-class user turn, so the agent answers
    and re-decides. Returns ``False`` when a concurrent decision already took it.
    """
    pending = live.pending_call_id
    if pending is None:
        return False
    call_id = _consume_pending_call(live, pending)
    if call_id is None:
        return False
    append_event(session, session_id, SessionEventKind.COMMAND_REJECTED, {"reason": _STEER_REASON})
    _resume_with_decision(
        session, registry, session_id, live, call_id, approved=False, reason=_STEER_REASON
    )
    return True


def _auto_approve(
    session: Session, registry: SessionRegistry, session_id: int, live: LiveSession
) -> bool:
    """Auto-approve the pending command (free-launch, FR-17 Slice 5) — one pass.

    Goes through the same compare-and-swap (:func:`_consume_pending_call`) as a human
    approval and records a ``command_approved`` flagged ``{"auto": True}`` so the
    transcript stays honest about what a human vetted. Returns ``False`` when a
    concurrent human decision already took the command.
    """
    pending = live.pending_call_id
    if pending is None:
        return False
    call_id = _consume_pending_call(live, pending)
    if call_id is None:
        return False
    append_event(session, session_id, SessionEventKind.COMMAND_APPROVED, {"auto": True})
    _resume_with_decision(session, registry, session_id, live, call_id, approved=True, reason="")
    return True


def _advance(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Drive the session forward at each turn boundary until it parks for the operator.

    Called after every turn boundary. Realises the message-routing invariant
    (ADR-0042): a queued operator message is always delivered at the next turn
    boundary — and if that boundary is an approval gate, the gate is pre-empted (the
    just-proposed command is set aside, :func:`_steer_pending_command`). In
    free-launch, a gate with no waiting message is auto-approved
    (:func:`_auto_approve`) instead — this subsumes the old free-launch loop.

    Iterative on purpose — one :func:`_resume_with_decision`/:func:`_resume_run` call
    per pass, never recursing — so a long run cannot blow the stack. Each resume
    drains the queued messages, so the loop converges: the session parks at a gated
    ``awaiting_command`` (no message, free-launch off), hands back in
    ``awaiting_operator`` (no message), is stopped, or is torn down. In gated mode
    with nothing queued the first pass returns immediately, so callers invoke it
    unconditionally.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session to drive.
    """
    while True:
        live = registry.get(session_id)
        if live is None or live.stopped:
            return  # torn down (concluded / ended) or operator-paused: do not advance
        record = session.get(RetestSessionRecord, session_id)
        if record is None:
            return
        status = RetestSessionStatus(record.status)
        if status is RetestSessionStatus.AWAITING_COMMAND:
            if live.has_queued_messages():
                if not _steer_pending_command(session, registry, session_id, live):
                    return
                continue
            if live.free_launch:
                if not _auto_approve(session, registry, session_id, live):
                    return
                continue
            return  # gated, nothing queued: park at the gate for the operator
        if status is RetestSessionStatus.AWAITING_OPERATOR and live.has_queued_messages():
            # A message arrived while the turn was in flight and the agent handed
            # back: deliver it now rather than stranding it (answer-when-done).
            _resume_run(session, registry, session_id, live)
            continue
        return  # working / handed back with nothing queued: nothing to advance


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

    An approval runs the command and resumes the agent; when the agent later
    exhausts the options it can think of it hands back for operator guidance
    (:func:`_dispatch_output`, ADR-0034) rather than running forever.

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
    # Drive the boundary: deliver a queued message, or (free-launch) auto-approve a
    # new proposal; in gated mode with nothing queued this returns immediately.
    _advance(session, registry, session_id)


def set_free_launch(
    session: Session, registry: SessionRegistry, session_id: int, enabled: bool
) -> None:
    """Toggle free-launch on a live session (FR-17 Slice 5).

    Updates the persisted mode + the live flag, records a ``free_launch_changed``
    transcript event, and — when enabling with a command already pending —
    auto-approves it (and any that follow) via :func:`_advance`. A no-op if
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
        _advance(session, registry, session_id)


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
    timeout: int = MAX_COMMAND_TIMEOUT,
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
        timeout: Per-command cap in seconds. Defaults to the hard ceiling — the
            operator ran it deliberately (it may be a slow scan) — and is clamped
            to that ceiling so even a manual command can never wedge the sandbox.
    """
    live = registry.get(session_id)
    if live is None:
        return
    result = live.sandbox.exec(command, timeout=clamp_timeout(timeout))
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
    """Record an operator chat message and buffer it for the agent's next turn (FR-17).

    Always recorded as a ``HUMAN_MESSAGE`` transcript event (so the chat shows it and
    it replays) — even for a session that outlived a backend restart and has no live
    agent, so a message is never silently lost (ADR-0042). When the session is live it
    is also buffered for delivery to the agent as a first-class user turn at the next
    turn boundary (:func:`_advance` / :func:`_resume_with_decision`) — the operator's
    *voice*, distinct from the `!` command path (:func:`submit_human_command`).

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (holds the message buffer).
        session_id: The retest session to message.
        text: The exact operator message.
    """
    append_event(session, session_id, SessionEventKind.HUMAN_MESSAGE, {"text": text})
    live = registry.get(session_id)
    if live is not None:
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
    ``end_session`` doesn't touch ``pending_call_id`` itself. Cancels any in-flight
    turn first (issue #204) so a wedged model call does not leave the run thread
    lingering after the session is gone; the row is already ``ended`` (terminal), so
    the cancelled turn's :class:`_TurnAbortedError` is swallowed by :func:`_fail`.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) in _TERMINAL:
        return
    set_status(session, session_id, RetestSessionStatus.ENDED)
    live = registry.get(session_id)
    if live is None:
        return
    live.request_cancel()
    with live.lock:
        _teardown(registry, session_id)


def restart_model(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Abort the in-flight turn and re-run it to unstick a wedged model (issue #204).

    The "restart model" console action. Asks the live session to cancel its current
    turn and re-run it (:meth:`LiveSession.request_restart`); a ``turn_restarted``
    marker is appended so the transcript shows the operator intervened. A no-op when
    the session is not live or no turn is in flight (nothing to unstick) — the
    console only offers the action while the agent is working.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session whose turn to restart.
    """
    live = registry.get(session_id)
    if live is None:
        return
    if live.request_restart():
        append_event(session, session_id, SessionEventKind.TURN_RESTARTED, {})


def _resume_run(
    session: Session, registry: SessionRegistry, session_id: int, live: LiveSession
) -> None:
    """Re-run the agent to continue after a hand-back (ADR-0034), one turn.

    Used by :func:`continue_session` and :func:`_advance` to resume the agent with
    any queued goal/chat steering (or a plain nudge) and dispatch its next output. A
    single turn: the caller runs :func:`_advance` to drive the boundary that follows.
    """
    set_status(session, session_id, RetestSessionStatus.WORKING)
    deps = _make_deps(session, session_id, live)
    goal, messages = live.drain_goal(), live.drain_messages()
    _mark_delivered(session, session_id, messages)
    user_prompt = _resume_prompt(goal, messages) or "Continue the retest toward a determination."
    try:
        result = run_agent_step(
            live.agent,
            user_prompt,
            session_id=session_id,
            deps=deps,
            message_history=live.messages,
            live=live,
        )
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)


#: The state a session resumes from when the operator messages it: the agent handed
#: control back (``awaiting_operator`` — a reply, a guided report, a recommendation,
#: or "I'm stuck", ADR-0034/0042). The sandbox stays alive and the operator's next
#: message re-runs the agent. ``stopped`` and ``awaiting_command`` resume by their own
#: paths (:func:`resume_session` / :func:`_steer_pending_command`).
_RESUMABLE_ON_MESSAGE: frozenset[RetestSessionStatus] = frozenset(
    {RetestSessionStatus.AWAITING_OPERATOR}
)


def continue_session(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Resume a session the agent handed back — ADR-0034 "Keep going" / reply (#204).

    A no-op unless the session is handed back in :data:`_RESUMABLE_ON_MESSAGE`
    (``awaiting_operator``) with a live agent — a handed-back session that outlived a
    backend restart has no sandbox to resume, so the operator restarts it instead. The
    agent only ever hands back between turns (never with a command still pending), so
    continuing re-runs it, folding in any queued goal/chat steering, then drives the
    boundary that follows (:func:`_advance`).

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The paused retest session to resume.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) not in _RESUMABLE_ON_MESSAGE:
        return
    live = registry.get(session_id)
    if live is None:
        return
    live.awaiting_guidance = False
    _resume_run(session, registry, session_id, live)
    _advance(session, registry, session_id)


def stop_session(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Operator pauses a running session — Stop (issue #150).

    A cooperative pause: sets the live ``stopped`` flag and moves the row to the
    non-terminal ``STOPPED`` state, keeping the sandbox alive. A command already
    running finishes (its output is recorded) and an in-flight agent step, on
    completion, parks in ``stopped`` rather than advancing (see
    :func:`_dispatch_output`); the free-launch loop halts (:func:`_advance`).
    A no-op if the session is not live or is already terminal or stopped.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) in _TERMINAL:
        return
    live = registry.get(session_id)
    if live is None or RetestSessionStatus(record.status) is RetestSessionStatus.STOPPED:
        return
    live.stopped = True
    set_status(session, session_id, RetestSessionStatus.STOPPED)


def resume_session(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Operator resumes a stopped session — Resume (issue #150).

    Clears the ``stopped`` flag and continues where the pause left off: if a
    command was held pending when the operator stopped, the gate re-opens
    (``awaiting_command``, then the free-launch loop drives it if enabled);
    otherwise the agent is re-run for its next step. A no-op unless the session
    is in ``STOPPED`` with a live agent (a stopped session that outlived a backend
    restart has no sandbox — the operator restarts it instead).
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) is not RetestSessionStatus.STOPPED:
        return
    live = registry.get(session_id)
    if live is None:
        return
    live.stopped = False
    if live.pending_call_id is not None:
        set_status(session, session_id, RetestSessionStatus.AWAITING_COMMAND)
        _advance(session, registry, session_id)
    else:
        _resume_run(session, registry, session_id, live)
        _advance(session, registry, session_id)


def resume_with_message_at_gate(
    session: Session, registry: SessionRegistry, session_id: int
) -> None:
    """Steer a command awaiting approval with an operator message (Claude-Code gate).

    The message-routing rule's gate case (ADR-0042): a message sent instead of
    approving withdraws the pending command and re-runs the agent with the message as
    a first-class user turn, then drives the boundary that follows. A no-op if the
    session is not live or a concurrent approve/reject already took the command — the
    message stays buffered (recorded by :func:`submit_message`) for the next boundary.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session whose gate the message steers.
    """
    live = registry.get(session_id)
    if live is None:
        return
    if _steer_pending_command(session, registry, session_id, live):
        _advance(session, registry, session_id)


def conclude_session(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    status: VerdictStatus,
    rationale: str,
) -> None:
    """Operator manually concludes a session with a determination — ADR-0034.

    The operator's own verdict, recordable at ANY live point in the retest (issue
    #150) — not only at an ``awaiting_operator`` hand-back: while a command awaits approval,
    or even while the agent is mid-step. Writes the verdict (``actor="operator"``,
    the only path that can record ``inconclusive``) and tears down the sandbox. A
    no-op if the session is already terminal. Works on an orphaned session too (the
    verdict is recorded; the teardown no-ops). When invoked mid-step, the in-flight
    agent turn's resulting failure is swallowed rather than clobbering this verdict
    (see :func:`_fail`).

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
    """Record an ``error`` event, set status ``error``, and tear down (orchestration boundary).

    Guards the conclude-anytime race (issue #150): the operator may conclude or end
    a session while an agent step is still in flight (``working``).
    Concluding tears the sandbox down, which makes the in-flight ``run_command`` raise —
    and that exception must NOT overwrite the operator's just-recorded verdict with an
    ``error``. So if the row is already terminal (a conclude/end committed on another
    DB session — hence the ``refresh`` to observe it), swallow the failure and only
    finish the teardown.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is not None:
        session.refresh(record)
        if RetestSessionStatus(record.status) in _TERMINAL:
            _teardown(registry, session_id)
            return
    append_event(session, session_id, SessionEventKind.ERROR, {"detail": detail})
    set_status(session, session_id, RetestSessionStatus.ERROR)
    _teardown(registry, session_id)
