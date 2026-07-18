"""Unit tests for plan persistence and the verdict-version stamp (FR-05)."""

import pytest
from sqlalchemy.orm import Session

from revalid.db import IN_MEMORY, PlanRecord, VerdictRecord, create_db_engine, session_factory
from revalid.domain import (
    Evidence,
    PlanStatus,
    Probe,
    RetestPlan,
    Verdict,
    VerdictStatus,
)


def _session() -> Session:
    return session_factory(create_db_engine(IN_MEMORY))()


def _plan() -> RetestPlan:
    probe = Probe(kind="planned-http", method="GET", url="http://localhost:3000/rest/x")
    return RetestPlan(finding_title="F", actions=(probe,), raw={"finding_title": "F"})


def test_plan_record_roundtrips_actions_and_status() -> None:
    with _session() as session:
        record = PlanRecord.from_plan(
            1, _plan(), version=2, status=PlanStatus.PROPOSED, origin="edited", rejected_actions=[]
        )
        session.add(record)
        session.commit()
        session.refresh(record)

        assert record.version == 2
        assert record.status == "proposed"
        assert record.origin == "edited"
        [probe] = record.probes()
        assert probe.url == "http://localhost:3000/rest/x"
        assert record.created_at is not None


def test_verdict_record_stamps_plan_version() -> None:
    verdict = Verdict(
        status=VerdictStatus.STILL_OPEN,
        reason_code="x",
        evidence=Evidence(request_method="GET", request_url="u", response_status=200),
    )
    record = VerdictRecord.from_domain(1, "planned-http", verdict, plan_id=7, plan_version=3)
    assert record.plan_id == 7
    assert record.plan_version == 3


def test_from_domain_is_batch_source() -> None:
    """A batch verdict row is tagged ``source="batch"`` with its evidence intact (Slice 6a)."""
    verdict = Verdict(
        status=VerdictStatus.FIXED,
        reason_code="patched",
        evidence=Evidence(request_method="GET", request_url="u", response_status=404),
    )
    record = VerdictRecord.from_domain(1, "planned-http", verdict)
    assert record.source == "batch"
    assert record.session_id is None
    assert record.evidence is not None
    assert record.to_domain() == verdict  # batch rows still round-trip


def test_agentic_constructor_builds_evidence_free_row() -> None:
    """``agentic()`` builds a session-linked, evidence-free verdict row (Slice 6a)."""
    record = VerdictRecord.agentic(
        finding_id=1,
        session_id=42,
        status=VerdictStatus.STILL_OPEN,
        rationale="agent found the bypass still works",
        actor="agent",
        reason_code="agentic_conclusion",
    )
    assert record.source == "agentic"
    assert record.session_id == 42
    assert record.finding_id == 1
    assert record.probe_kind == "agentic"
    assert record.status == "still_open"
    assert record.rationale == "agent found the bypass still works"
    assert record.actor == "agent"
    assert record.reason_code == "agentic_conclusion"
    assert record.evidence is None
    assert record.matched_indicators == []


def test_to_domain_rejects_agentic_row() -> None:
    """``to_domain()`` is batch-only — an agentic (evidence-free) row raises (Slice 6a)."""
    record = VerdictRecord.agentic(
        finding_id=1,
        session_id=7,
        status=VerdictStatus.FIXED,
        rationale="fixed",
        actor="operator",
        reason_code="operator_adjudication",
    )
    with pytest.raises(ValueError, match="batch-only"):
        record.to_domain()


def test_agentic_constructor_stores_evidence() -> None:
    """The agentic() constructor persists a flexible evidence dict (Slice 6b-i)."""
    from revalid.domain import AgenticEvidence

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
    """An agentic row commits with a NULL ``evidence`` column (nullability, Slice 6a)."""
    with _session() as session:
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
    assert record.source == "agentic"
