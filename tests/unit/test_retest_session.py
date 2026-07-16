"""Unit tests for the FR-17 retest-session persistence layer (ADR-0025, Slice 0).

In-memory SQLite, no I/O. Covers the append-only transcript (monotonic ``seq``),
status transitions, and verdict recording on :class:`~revalid.db.RetestSessionRecord`.
"""

from __future__ import annotations

from revalid import retest_session as rs
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import (
    Finding,
    RetestSessionStatus,
    SessionEventKind,
    Severity,
    VerdictStatus,
)
from revalid.findings import create_finding


def _seed_finding(session) -> int:  # type: ignore[no-untyped-def]
    record = create_finding(
        session, Finding(title="SQLi", severity=Severity.HIGH, description="login bypass")
    )
    session.commit()  # create_finding only flushes; the caller commits
    return record.id


def test_create_session_starts_in_starting_status() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        assert s.id is not None
        assert s.finding_id == fid
        assert s.status == RetestSessionStatus.STARTING.value
        assert s.model == "ollama:qwen3.6:27b"
        assert s.verdict_status is None
        assert s.ended_at is None


def test_append_event_assigns_monotonic_seq() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "starting"})
        rs.append_event(session, s.id, SessionEventKind.COMMAND_PROPOSED, {"command": "id"})
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["kind"] == "command_proposed"
    assert events[1]["payload"]["command"] == "id"


def test_load_events_after_filters_by_seq() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "starting"})
        rs.append_event(session, s.id, SessionEventKind.COMMAND_PROPOSED, {"command": "id"})
        events = rs.load_events_after(session, s.id, after_seq=1)
    assert [e["seq"] for e in events] == [2]


def test_set_status_updates_row_and_appends_state_change_event() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.set_status(session, s.id, RetestSessionStatus.AWAITING_COMMAND)
        session.refresh(s)
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
    assert len(events) == 1
    assert events[0]["kind"] == "state_change"
    assert events[0]["payload"] == {"to": "awaiting_command"}


def test_set_status_on_unknown_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        rs.set_status(session, 999, RetestSessionStatus.ENDED)  # must not raise


def test_record_verdict_writes_row_fields() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.record_verdict(session, s.id, VerdictStatus.STILL_OPEN, "auth still bypassable")
        session.refresh(s)
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_status == "still_open"
    assert s.verdict_rationale == "auth still bypassable"
    assert s.ended_at is not None
    assert events[-1]["kind"] == "verdict"
    assert events[-1]["payload"] == {
        "status": "still_open",
        "rationale": "auth still bypassable",
    }


def test_record_verdict_on_unknown_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        rs.record_verdict(session, 999, VerdictStatus.FIXED, "n/a")  # must not raise
