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
from revalid.retest_agent import ConcludeOutput, RetestSessionDeps
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


def create_session(session: Session, *, finding_id: int, model: str) -> RetestSessionRecord:
    """Insert a ``starting`` session row and return it."""
    record = RetestSessionRecord(
        finding_id=finding_id, status=RetestSessionStatus.STARTING.value, model=model
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
    """Persist the agent verdict on the session row + a ``verdict`` transcript event."""
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    record.status = RetestSessionStatus.CONCLUDED.value
    record.verdict_status = status.value
    record.verdict_rationale = rationale
    record.ended_at = func.now()
    session.commit()
    append_event(
        session,
        session_id,
        SessionEventKind.VERDICT,
        {"status": status.value, "rationale": rationale},
    )


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
    """

    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]
    sandbox: Sandbox
    messages: list[ModelMessage] = field(default_factory=list)
    pending_call_id: str | None = None
    step_count: int = 0
    max_steps: int = 8


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


def _make_deps(session: Session, session_id: int, sandbox: Sandbox) -> RetestSessionDeps:
    """Build agent deps whose output callback appends a ``command_output`` event.

    Rebuilt fresh, immediately before every ``agent.run_sync`` call — see the
    correctness note on :class:`LiveSession`. The returned ``emit_output``
    closure is only ever invoked synchronously inside that same call, so
    binding it to ``session`` here is safe.
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

    return RetestSessionDeps(sandbox=sandbox, emit_output=emit)


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
        call = output.approvals[0]
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
        set_status(session, session_id, RetestSessionStatus.AWAITING_COMMAND)
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
    """
    sandbox.start()
    live = LiveSession(agent=agent, sandbox=sandbox, max_steps=max_steps)
    registry.put(session_id, live)
    set_status(session, session_id, RetestSessionStatus.STARTING)
    deps = _make_deps(session, session_id, sandbox)
    try:
        result = agent.run_sync(finding_prompt, deps=deps)
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)


def _step_budget_exhausted(live: LiveSession) -> bool:
    """Count one more approved command and report whether the budget is now exceeded."""
    live.step_count += 1
    return live.step_count > live.max_steps


def apply_decision(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    *,
    approved: bool,
    reason: str = "",
) -> None:
    """Resume a paused run with a human decision on the pending command.

    Each *approval* counts against ``live.max_steps``; exceeding it force-
    concludes the session ``inconclusive`` and tears the sandbox down WITHOUT
    running the over-budget command (the budget backstop bounds an
    always-proposing agent). Rejections never run a command, so they never
    count against the budget.

    Args:
        session: Active DB session for this call — always freshly obtained by
            the caller, never held across separate orchestration calls.
        registry: The live-session registry.
        session_id: The retest session to resume.
        approved: Whether the pending command was approved.
        reason: Optional human-supplied reason, recorded and (on rejection)
            surfaced back to the model as the tool's denial message.
    """
    live = registry.get(session_id)
    if live is None or live.pending_call_id is None:
        return
    kind = SessionEventKind.COMMAND_APPROVED if approved else SessionEventKind.COMMAND_REJECTED
    append_event(session, session_id, kind, {"reason": reason} if reason else {})

    if approved and _step_budget_exhausted(live):
        record_verdict(session, session_id, VerdictStatus.INCONCLUSIVE, "budget exhausted")
        _mark_given_up(session, session_id)
        _teardown(registry, session_id)
        return

    set_status(session, session_id, RetestSessionStatus.RUNNING_COMMAND)
    results = DeferredToolResults()
    results.approvals[live.pending_call_id] = ToolApproved() if approved else ToolDenied(reason)
    live.pending_call_id = None
    deps = _make_deps(session, session_id, live.sandbox)
    try:
        result = live.agent.run_sync(
            deps=deps, message_history=live.messages, deferred_tool_results=results
        )
    except Exception as exc:  # broad on purpose: orchestration boundary, records + tears down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)


def end_session(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Operator-initiated end: tear down and mark ``ended`` (no-op if already terminal)."""
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) in _TERMINAL:
        return
    set_status(session, session_id, RetestSessionStatus.ENDED)
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
