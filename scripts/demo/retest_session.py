"""Demo for FR-17: drive one agentic retest session through the approval gate, offline.

Usage::

    make demo-retest-session

Runs fully offline — no Docker, no lab, no LLM — with a :class:`~revalid.sandbox.FakeSandbox`
standing in for the live ``DockerSandbox`` and a Pydantic AI ``FunctionModel`` standing in for
the configured LLM backend. Shows the full ADR-0025 Slice 0 cycle: the agent proposes one
shell command, a human approves it, the sandbox runs it, and the agent concludes with a
verdict — all persisted as an append-only transcript. A real run against the lab needs the
``sandbox`` extra, `make lab-up`, and an LLM backend — see
``tests/system/test_retest_session_system.py``.
"""

from __future__ import annotations

from pydantic_ai.models.function import FunctionModel
from sqlalchemy.orm import Session
from tests._retest_helpers import script_run_then_conclude

from revalid.db import IN_MEMORY, RetestSessionRecord, create_db_engine, session_factory
from revalid.domain import Finding, Severity
from revalid.findings import create_finding
from revalid.retest_agent import build_retest_agent
from revalid.retest_session import (
    SessionRegistry,
    apply_decision,
    create_session,
    load_events_after,
    start_and_step,
)
from revalid.sandbox import CommandResult, FakeSandbox


def _run_scripted_session(
    session: Session, registry: SessionRegistry, finding_id: int
) -> RetestSessionRecord:
    """Create a session and drive it through propose -> approve -> output -> verdict.

    Args:
        session: The active DB session.
        registry: The live-session registry driving orchestration.
        finding_id: The finding to retest.

    Returns:
        The (refreshed) retest-session record, now in its terminal ``concluded`` status.
    """
    box = FakeSandbox(
        [CommandResult(stdout='{"status":"ok"}', stderr="", exit_code=0, elapsed_ms=42)]
    )
    agent = build_retest_agent(FunctionModel(script_run_then_conclude))

    record = create_session(session, finding_id=finding_id, model="function-model:demo")
    start_and_step(
        session, registry, record.id, agent, box, "Retest the SQLi login-bypass finding."
    )
    # The proposed command's ``tool_call_id`` is the ``cid`` the UI approves against
    # (from the ``command_proposed`` transcript event); resolve it the same way here.
    proposed = next(
        event
        for event in load_events_after(session, record.id, after_seq=0)
        if event["kind"] == "command_proposed"
    )
    command_id = str(proposed["payload"]["tool_call_id"])
    apply_decision(session, registry, record.id, approved=True, command_id=command_id)
    session.refresh(record)
    return record


def main() -> int:
    """Run one scripted retest session end-to-end and print its transcript."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()

    with sessions() as session:
        finding = Finding(title="SQLi", severity=Severity.HIGH, description="login bypass via SQLi")
        finding_id = create_finding(session, finding).id
        session.commit()
        print(f"1. seeded finding #{finding_id}: {finding.title}")

        record = _run_scripted_session(session, registry, finding_id)
        print(f"2. session #{record.id} concluded, status={record.status}")

        events = load_events_after(session, record.id, after_seq=0)

    print("3. transcript (proposed -> approved -> output -> verdict):")
    for event in events:
        print(f"   [{event['seq']}] {event['kind']:<18} {event['payload']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
