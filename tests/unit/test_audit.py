"""Unit tests for FR-10 audit-trail re-derivation (ADR-0025/0030).

Acceptance criterion: a re-derivation routine reproduces every stored (agentic)
verdict from the audit trail alone — its session transcript — with no
re-execution. (Batch verdict re-derivation was removed with the batch path, 6b-iii.)
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid import retest_session as rs
from revalid.audit import rederive_run
from revalid.db import IN_MEMORY, VerdictRecord, create_db_engine, session_factory
from revalid.domain import Finding, Severity, VerdictStatus
from revalid.findings import create_finding


def _session() -> Session:
    session = session_factory(create_db_engine(IN_MEMORY))()
    create_finding(session, Finding(title="F", severity=Severity.HIGH))
    session.commit()
    return session


def test_rederive_run_is_empty_when_no_verdicts() -> None:
    with _session() as session:
        report = rederive_run(session)
        assert report.total == 0
        assert report.reproduced == 0
        assert report.ok


def test_rederive_reproduces_an_agentic_verdict() -> None:
    """An agentic verdict re-derives from its session transcript (FR-10)."""
    with _session() as session:
        sid = rs.create_session(session, finding_id=1, model="m").id
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
        report = rederive_run(session)
        assert report.total == 1
        assert report.ok


def test_rederive_flags_a_tampered_agentic_verdict() -> None:
    """A stored agentic row that drifts from its transcript verdict is a discrepancy."""
    with _session() as session:
        sid = rs.create_session(session, finding_id=1, model="m").id
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
        row = session.scalars(select(VerdictRecord)).one()
        row.status = "fixed"  # drift the stored row away from the transcript
        session.commit()
        report = rederive_run(session)
        assert not report.ok
        [discrepancy] = report.discrepancies
        assert discrepancy.stored.startswith("fixed")
        assert "still_open" in discrepancy.rederived


def test_rederive_flags_a_rationale_only_drift() -> None:
    """A stored agentic row whose rationale drifts (status intact) is still a discrepancy."""
    with _session() as session:
        sid = rs.create_session(session, finding_id=1, model="m").id
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
        row = session.scalars(select(VerdictRecord)).one()
        row.rationale = "tampered rationale"  # status unchanged, rationale drifts
        session.commit()
        report = rederive_run(session)
        assert not report.ok
        [discrepancy] = report.discrepancies
        assert "tampered rationale" in discrepancy.stored
        assert "auth still bypassable" in discrepancy.rederived


def test_rederive_checks_operator_row_against_adjudication_event() -> None:
    """An operator adjudication is audited against its ``verdict_adjudicated`` event."""
    with _session() as session:
        sid = rs.create_session(session, finding_id=1, model="m").id
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "agent verdict")
        rs.adjudicate_verdict(session, sid, VerdictStatus.FIXED, "human override")
        report = rederive_run(session)
        assert report.total == 2  # the agent row + the operator row
        assert report.ok  # each matches its own transcript event
