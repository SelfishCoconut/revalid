"""Versioned, schema-validated run export (FR-12).

A *run* is the full state a revalidation produces — the uploaded reports, the
findings extracted from them, every retest-plan version, and every
evidence-backed verdict. :func:`build_export` assembles that state into a single
:class:`RunExport`: a self-contained, versioned JSON document the evaluation
harness (FR-15) consumes.

The document is *versioned* by :data:`SCHEMA_VERSION` — bumped whenever the
export shape changes — so a consumer can tell which contract a file follows.
:func:`export_schema` emits the JSON Schema the document validates against; it is
generated from these models (never hand-written) and published to
``docs/reference/schemas/`` via ``make export-schema``, guarded against drift by
``tests/unit/test_export.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid import __version__
from revalid.db import FindingRecord, PlanRecord, ReportRecord, VerdictRecord
from revalid.domain import Finding, Probe, Verdict, VerdictStatus
from revalid.findings import list_notes, list_versions

# Export-format version (independent of the tool's release version). Bump on any
# breaking change to the RunExport shape; the JSON Schema carries the same value.
SCHEMA_VERSION = "1.1"


class Generator(BaseModel):
    """Provenance of the tool that produced the export (NFR-02 lineage)."""

    model_config = ConfigDict(frozen=True)

    tool: str
    version: str


class ReportExport(BaseModel):
    """An uploaded report and its ingest outcome (FR-01)."""

    model_config = ConfigDict(frozen=True)

    id: int
    filename: str
    status: str
    model: str
    finding_count: int
    created_at: datetime


class NoteExport(BaseModel):
    """One stage-tagged operator note on a finding (FR-16)."""

    model_config = ConfigDict(frozen=True)

    id: int
    stage: str
    body: str
    author: str
    created_at: datetime


class FindingVersionExport(BaseModel):
    """One immutable version of a finding's content (FR-16).

    ``origin`` is ``extraction`` for version 1 and ``edit`` for operator
    revisions; ``edited_by``/``reason`` carry the edit lineage.
    """

    model_config = ConfigDict(frozen=True)

    version: int
    origin: str
    edited_by: str | None
    reason: str
    created_at: datetime
    finding: Finding


class FindingExport(BaseModel):
    """A persisted finding: current content plus full version history and notes (FR-02/03/16).

    ``finding`` is the *current* version's content (the field pre-FR-16 consumers
    read); ``version`` names which version that is; ``versions`` is the complete
    append-only history (oldest first, extraction = v1) and ``notes`` the
    stage-tagged annotation log (chronological) — the audit-grade record (FR-10).
    """

    model_config = ConfigDict(frozen=True)

    id: int
    report_id: int | None
    version: int
    finding: Finding
    versions: tuple[FindingVersionExport, ...]
    notes: tuple[NoteExport, ...]


class PlanExport(BaseModel):
    """One retest-plan version with its approval decision (FR-04/FR-05)."""

    model_config = ConfigDict(frozen=True)

    id: int
    finding_id: int
    version: int
    status: str
    origin: str
    actions: tuple[Probe, ...]
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None


class VerdictExport(BaseModel):
    """A verdict with its evidence and audit stamps (FR-09/FR-10)."""

    model_config = ConfigDict(frozen=True)

    id: int
    finding_id: int
    probe_kind: str
    plan_id: int | None
    plan_version: int | None
    actor: str
    created_at: datetime
    verdict: Verdict


class RunMetrics(BaseModel):
    """Descriptive counts and timing over the exported run (FR-15 denominators).

    Neutral facts about the run, not correctness scores: the tool has no ground
    truth, so grading (correct/wrong) is the evaluation harness's job (FR-15).
    ``verdicts_by_status`` always carries every :class:`VerdictStatus` key so the
    shape is stable across runs.
    """

    model_config = ConfigDict(frozen=True)

    reports: int
    findings: int
    plans: int
    verdicts: int
    verdicts_by_status: dict[str, int]
    total_elapsed_ms: float
    mean_elapsed_ms: float


class RunExport(BaseModel):
    """A complete revalidation run as one versioned JSON document (FR-12)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str
    generated_at: datetime
    generator: Generator
    reports: tuple[ReportExport, ...]
    findings: tuple[FindingExport, ...]
    plans: tuple[PlanExport, ...]
    verdicts: tuple[VerdictExport, ...]
    metrics: RunMetrics


def _report_export(record: ReportRecord) -> ReportExport:
    return ReportExport(
        id=record.id,
        filename=record.filename,
        status=record.status,
        model=record.model,
        finding_count=record.finding_count,
        created_at=record.created_at,
    )


def _finding_export(session: Session, record: FindingRecord) -> FindingExport | None:
    """Assemble a finding's export: current content + full version history + notes (FR-16)."""
    versions = list_versions(session, record.id)
    if not versions:  # pragma: no cover - a finding always has >=1 version
        return None
    current = versions[-1]
    return FindingExport(
        id=record.id,
        report_id=record.report_id,
        version=current.version,
        finding=current.to_domain(),
        versions=tuple(
            FindingVersionExport(
                version=v.version,
                origin=v.origin,
                edited_by=v.edited_by,
                reason=v.reason,
                created_at=v.created_at,
                finding=v.to_domain(),
            )
            for v in versions
        ),
        notes=tuple(
            NoteExport(
                id=n.id, stage=n.stage, body=n.body, author=n.author, created_at=n.created_at
            )
            for n in sorted(list_notes(session, record.id), key=lambda note: note.id)
        ),
    )


def _plan_export(record: PlanRecord) -> PlanExport:
    return PlanExport(
        id=record.id,
        finding_id=record.finding_id,
        version=record.version,
        status=record.status,
        origin=record.origin,
        actions=record.probes(),
        created_at=record.created_at,
        decided_at=record.decided_at,
        decided_by=record.decided_by,
    )


def _verdict_export(record: VerdictRecord) -> VerdictExport:
    return VerdictExport(
        id=record.id,
        finding_id=record.finding_id,
        probe_kind=record.probe_kind,
        plan_id=record.plan_id,
        plan_version=record.plan_version,
        actor=record.actor,
        created_at=record.created_at,
        verdict=record.to_domain(),
    )


def _metrics(
    reports: tuple[ReportExport, ...],
    findings: tuple[FindingExport, ...],
    plans: tuple[PlanExport, ...],
    verdicts: tuple[VerdictExport, ...],
) -> RunMetrics:
    by_status = {status.value: 0 for status in VerdictStatus}
    for verdict in verdicts:
        by_status[verdict.verdict.status.value] += 1
    total_ms = sum(v.verdict.evidence.elapsed_ms for v in verdicts)
    return RunMetrics(
        reports=len(reports),
        findings=len(findings),
        plans=len(plans),
        verdicts=len(verdicts),
        verdicts_by_status=by_status,
        total_elapsed_ms=total_ms,
        mean_elapsed_ms=total_ms / len(verdicts) if verdicts else 0.0,
    )


def build_export(session: Session, *, generated_at: datetime | None = None) -> RunExport:
    """Assemble the full run — reports, findings, plans, verdicts — for export (FR-12).

    Reads every entity in id order (deterministic output) and derives the run
    metrics. Purely a read: it opens no network and mutates nothing.

    Args:
        session: Session over the database to export.
        generated_at: Stamp for the document; defaults to the current UTC time
            (injectable so tests and re-runs are deterministic).

    Returns:
        The complete run as a :class:`RunExport`, valid against
        :func:`export_schema`.
    """
    reports = tuple(
        _report_export(r) for r in session.scalars(select(ReportRecord).order_by(ReportRecord.id))
    )
    findings = tuple(
        export
        for r in session.scalars(select(FindingRecord).order_by(FindingRecord.id))
        if (export := _finding_export(session, r)) is not None
    )
    plans = tuple(
        _plan_export(r) for r in session.scalars(select(PlanRecord).order_by(PlanRecord.id))
    )
    verdicts = tuple(
        _verdict_export(r)
        for r in session.scalars(select(VerdictRecord).order_by(VerdictRecord.id))
    )
    return RunExport(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at if generated_at is not None else datetime.now(UTC),
        generator=Generator(tool="revalid", version=__version__),
        reports=reports,
        findings=findings,
        plans=plans,
        verdicts=verdicts,
        metrics=_metrics(reports, findings, plans, verdicts),
    )


def export_schema() -> dict[str, Any]:
    """Return the published JSON Schema the export validates against (FR-12).

    Generated from :class:`RunExport` so the contract can never drift from the
    document it describes.
    """
    return RunExport.model_json_schema()
