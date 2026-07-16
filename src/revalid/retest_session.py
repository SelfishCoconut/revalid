"""FR-17 / M6 retest-session persistence (ADR-0025, Slice 0).

An agentic retest session is a :class:`~revalid.db.RetestSessionRecord` row plus
its append-only transcript of :class:`~revalid.db.SessionEventRecord` rows,
symmetric with how :mod:`revalid.findings` splits identity from immutable
history. Task 3 adds this persistence layer only; the agent-driving
orchestration (``LiveSession``, ``start_and_step``, ...) lands in Task 5.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revalid.db import RetestSessionRecord, SessionEventRecord
from revalid.domain import RetestSessionStatus, SessionEventKind, VerdictStatus

_TERMINAL: frozenset[RetestSessionStatus] = frozenset(
    {
        RetestSessionStatus.CONCLUDED,
        RetestSessionStatus.GIVEN_UP,
        RetestSessionStatus.ENDED,
        RetestSessionStatus.ERROR,
    }
)


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
