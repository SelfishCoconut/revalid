"""Unit tests for plan persistence and the verdict-version stamp (FR-05)."""

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
