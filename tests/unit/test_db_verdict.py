"""Unit tests for the agentic VerdictRecord (FR-09/FR-17).

The batch plan/verdict persistence retired with the batch path (FR-17 6b-iii);
every verdict is now a retest-session conclusion built via ``agentic()``.
"""

from revalid.db import IN_MEMORY, VerdictRecord, create_db_engine, session_factory
from revalid.domain import AgenticEvidence, VerdictStatus


def test_agentic_constructor_builds_a_session_linked_row() -> None:
    record = VerdictRecord.agentic(
        finding_id=1,
        session_id=42,
        status=VerdictStatus.STILL_OPEN,
        rationale="agent found the bypass still works",
        actor="agent",
        reason_code="agentic_conclusion",
    )
    assert record.finding_id == 1
    assert record.session_id == 42
    assert record.status == "still_open"
    assert record.rationale == "agent found the bypass still works"
    assert record.actor == "agent"
    assert record.reason_code == "agentic_conclusion"
    assert record.evidence is None
    assert record.matched_indicators == []


def test_agentic_constructor_stores_evidence() -> None:
    evidence = AgenticEvidence(
        explanation="still open", command="curl -s http://lab/x", output="{token}", exit_code=0
    )
    record = VerdictRecord.agentic(
        finding_id=1,
        session_id=5,
        status=VerdictStatus.STILL_OPEN,
        rationale="still open",
        actor="agent",
        reason_code="agentic_conclusion",
        evidence=evidence.model_dump(),
    )
    assert record.evidence is not None
    assert record.evidence["command"] == "curl -s http://lab/x"
    assert record.evidence["explanation"] == "still open"


def test_agentic_row_persists_with_null_evidence() -> None:
    with session_factory(create_db_engine(IN_MEMORY))() as session:
        record = VerdictRecord.agentic(
            finding_id=1,
            session_id=7,
            status=VerdictStatus.INCONCLUSIVE,
            rationale="budget exhausted",
            actor="agent",
            reason_code="agentic_conclusion",
        )
        session.add(record)
        session.commit()
        session.refresh(record)
    assert record.id is not None
    assert record.evidence is None
