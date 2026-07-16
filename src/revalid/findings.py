"""Finding revision & annotation: versioned findings + stage-tagged notes (FR-16, ADR-0024).

A finding is a stable identity (:class:`~revalid.db.FindingRecord`) plus
append-only immutable version rows (:class:`~revalid.db.FindingVersionRecord`):
extraction is version 1, each operator edit a new version — symmetric with the
FR-05 plan model (ADR-0012). Plans and verdicts reference the stable identity, so
amending a finding never orphans them. Notes
(:class:`~revalid.db.FindingNoteRecord`) are a per-finding, stage-tagged,
append-only log. This module owns those lifecycles; nothing mutates a version or a
note in place.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.db import FindingNoteRecord, FindingRecord, FindingVersionRecord
from revalid.domain import Finding, FindingOrigin, FindingStage

_ACTOR = "user"


def _next_version(session: Session, finding_id: int) -> int:
    versions = session.scalars(
        select(FindingVersionRecord.version).where(FindingVersionRecord.finding_id == finding_id)
    ).all()
    return max(versions) + 1 if versions else 1


def create_finding(
    session: Session, finding: Finding, report_id: int | None = None
) -> FindingRecord:
    """Create a finding identity and its version-1 content (``origin=extraction``).

    The single entry point for a newly ingested finding — extraction, FR-02
    import, and manual entry all land here. Flushes so the version row can
    reference the new identity id, but leaves the ``commit`` to the caller
    (findings are created in a batch alongside their report).
    """
    record = FindingRecord(report_id=report_id)
    session.add(record)
    session.flush()
    session.add(
        FindingVersionRecord.from_domain(
            record.id, finding, version=1, origin=FindingOrigin.EXTRACTION
        )
    )
    return record


def add_version(
    session: Session,
    finding_id: int,
    finding: Finding,
    *,
    edited_by: str = _ACTOR,
    reason: str = "",
) -> FindingVersionRecord:
    """Append an operator edit as a new immutable version (``origin=edit``).

    Never mutates a prior version — the earlier content stays in history as the
    correction record (FR-10). The appended version becomes the current one.
    """
    record = FindingVersionRecord.from_domain(
        finding_id,
        finding,
        version=_next_version(session, finding_id),
        origin=FindingOrigin.EDIT,
        edited_by=edited_by,
        reason=reason,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def current_version(session: Session, finding_id: int) -> FindingVersionRecord | None:
    """Return the finding's current (highest-version) content, or ``None``."""
    return session.scalars(
        select(FindingVersionRecord)
        .where(FindingVersionRecord.finding_id == finding_id)
        .order_by(FindingVersionRecord.version.desc())
    ).first()


def list_versions(session: Session, finding_id: int) -> list[FindingVersionRecord]:
    """Return every version of a finding, oldest first (extraction = v1)."""
    return list(
        session.scalars(
            select(FindingVersionRecord)
            .where(FindingVersionRecord.finding_id == finding_id)
            .order_by(FindingVersionRecord.version)
        )
    )


def add_note(
    session: Session,
    finding_id: int,
    stage: FindingStage,
    body: str,
    *,
    author: str = _ACTOR,
) -> FindingNoteRecord:
    """Append a stage-tagged note to the finding's log (append-only)."""
    record = FindingNoteRecord(finding_id=finding_id, stage=stage.value, body=body, author=author)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_notes(session: Session, finding_id: int) -> list[FindingNoteRecord]:
    """Return the finding's notes, newest first."""
    return list(
        session.scalars(
            select(FindingNoteRecord)
            .where(FindingNoteRecord.finding_id == finding_id)
            .order_by(FindingNoteRecord.created_at.desc(), FindingNoteRecord.id.desc())
        )
    )
