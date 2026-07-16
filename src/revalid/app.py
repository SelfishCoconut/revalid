"""FastAPI application factory (ADR-0002: local single-user web app).

Run locally with::

    uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1

The app must only ever bind to 127.0.0.1 (NFR-03); there is no
authentication in TFG scope.
"""

from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, cast

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from revalid import __version__
from revalid.allowlist import load_allowlist
from revalid.approval import (
    AllActionsRejectedError,
    NoProposedPlanError,
    PlanNotApprovedError,
    approve_plan,
    edit_plan,
    execute_approved_plan,
    finish_plan_generation,
    list_plans,
    reject_plan,
    revise_plan,
    start_plan_generation,
)
from revalid.audit import rederive_run
from revalid.browser import BrowserProbeUnavailableError, BrowserRunner, make_browser_runner
from revalid.db import (
    FindingNoteRecord,
    FindingRecord,
    FindingVersionRecord,
    PlanRecord,
    ReportRecord,
    VerdictRecord,
    create_db_engine,
    session_factory,
)
from revalid.domain import (
    Finding,
    FindingStage,
    Probe,
    ReportStatus,
    RetestPlan,
    Settings,
    Severity,
    Verdict,
)
from revalid.export import RunExport, build_export, export_schema
from revalid.extract import ExtractedFinding, build_extraction_agent, extract_report
from revalid.findings import (
    add_note,
    add_version,
    create_finding,
    current_version,
    list_notes,
    list_versions,
)
from revalid.ingest import IngestError, map_defectdojo_export
from revalid.llm import agent_model_name, build_model
from revalid.pdf import PdfError, read_pdf
from revalid.plan import PlannedAction, PlanResult, RejectedAction, build_plan_agent, generate_plan
from revalid.retest import build_probe_client, lab_base_url
from revalid.sanity import PlanDeviationError
from revalid.settings import ProbeResult, load_or_seed, probe_provider, save

# Repo-root-relative location of the built SPA (frontend/dist); served at "/"
# when present (FR-11). Absent in backend-only dev and CI unit runs.
_SPA_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class FindingOut(Finding):
    """A persisted finding as returned by the API — the *current* version's content.

    ``version`` is the current version number (extraction = 1); it bumps on every
    operator edit (FR-16). ``id`` is the stable finding identity plans/verdicts
    reference.
    """

    id: int
    report_id: int | None = None
    version: int = 1


class FindingVersionOut(Finding):
    """One immutable finding version as returned by the API (FR-16)."""

    version: int
    origin: str
    edited_by: str | None = None
    reason: str = ""
    created_at: datetime

    @classmethod
    def from_record(cls, record: FindingVersionRecord) -> "FindingVersionOut":
        """Build the API view from a persisted finding-version row."""
        return cls(
            version=record.version,
            origin=record.origin,
            edited_by=record.edited_by,
            reason=record.reason,
            created_at=record.created_at,
            **record.to_domain().model_dump(),
        )


class FindingEditIn(BaseModel):
    """An operator edit of a finding's content → a new immutable version (FR-16)."""

    title: str = Field(min_length=1)
    severity: Severity
    description: str = ""
    impact: str = ""
    attack_vector: str = ""
    affected_endpoints: tuple[str, ...] = ()
    reproduction_steps: tuple[str, ...] = ()
    reason: str = ""

    def to_finding(self, base_raw: dict[str, Any]) -> Finding:
        """Build the domain finding for this edit, carrying forward prior lineage.

        ``base_raw`` is the current version's ``raw`` (extraction lineage), kept so
        editing never discards how the finding was originally produced (FR-10).
        """
        return Finding(
            title=self.title,
            severity=self.severity,
            description=self.description,
            impact=self.impact,
            attack_vector=self.attack_vector,
            affected_endpoints=self.affected_endpoints,
            reproduction_steps=self.reproduction_steps,
            raw=base_raw,
        )


class NoteIn(BaseModel):
    """An operator note on a finding, tagged with the stage it was written on (FR-16)."""

    stage: FindingStage = FindingStage.GENERAL
    body: str = Field(min_length=1)


class NoteOut(BaseModel):
    """A persisted finding note as returned by the API (FR-16)."""

    id: int
    finding_id: int
    stage: str
    body: str
    author: str
    created_at: datetime

    @classmethod
    def from_record(cls, record: FindingNoteRecord) -> "NoteOut":
        """Build the API view from a persisted note row."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            stage=record.stage,
            body=record.body,
            author=record.author,
            created_at=record.created_at,
        )


class VerdictOut(Verdict):
    """A persisted verdict as returned by the API (domain model + linkage)."""

    id: int
    finding_id: int
    probe_kind: str
    plan_version: int | None = None


class DiscrepancyOut(BaseModel):
    """A stored verdict that no longer re-derives from its evidence (FR-10)."""

    verdict_id: int
    finding_id: int
    stored: str
    rederived: str


class AuditOut(BaseModel):
    """Result of re-deriving every verdict from the stored audit trail (FR-10)."""

    total: int
    reproduced: int
    ok: bool
    discrepancies: list[DiscrepancyOut]


class PlanRequest(BaseModel):
    """Optional body for plan generation: operator guidance for this run (FR-04)."""

    instructions: str = ""


class PlanOut(BaseModel):
    """A persisted retest-plan version as returned by the API (FR-05)."""

    id: int
    finding_id: int
    version: int
    status: str
    origin: str
    error: str | None
    actions: tuple[Probe, ...]
    rejected_actions: tuple[RejectedAction, ...]
    raw: dict[str, Any]
    decided_at: datetime | None
    decided_by: str | None

    @classmethod
    def from_record(cls, record: PlanRecord) -> "PlanOut":
        """Build the API view from a persisted plan row."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            version=record.version,
            status=record.status,
            origin=record.origin,
            error=record.error,
            actions=record.probes(),
            rejected_actions=tuple(
                RejectedAction.model_validate(item) for item in record.rejected_actions
            ),
            raw=record.raw,
            decided_at=record.decided_at,
            decided_by=record.decided_by,
        )


class ImportResult(BaseModel):
    """Outcome of a findings import."""

    imported: int


class ReportOut(BaseModel):
    """A persisted report / ingest job as returned by the API (FR-01/FR-11)."""

    id: int
    filename: str
    status: str
    model: str
    error: str | None
    finding_count: int
    created_at: datetime

    @classmethod
    def from_record(cls, record: ReportRecord) -> "ReportOut":
        """Build the API view from a persisted report row."""
        return cls(
            id=record.id,
            filename=record.filename,
            status=record.status,
            model=record.model,
            error=record.error,
            finding_count=record.finding_count,
            created_at=record.created_at,
        )


class SettingsOut(BaseModel):
    """Public view of the model/provider setting; the key is write-only (ADR-0021)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    base_url: str | None
    api_key_set: bool
    api_key_hint: str | None

    @classmethod
    def from_domain(cls, cfg: Settings) -> "SettingsOut":
        """Build the masked view: the key becomes a boolean + last-4 hint only."""
        key = cfg.api_key or ""
        return cls(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key_set=bool(key),
            api_key_hint=key[-4:] if key else None,
        )


class SettingsUpdateIn(BaseModel):
    """Settings update payload; a blank ``api_key`` keeps the stored one (ADR-0021)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    clear_key: bool = False


class ProbeIn(BaseModel):
    """Probe request: which endpoint (and optional key) to discover models from."""

    base_url: str | None = None
    api_key: str | None = None


def get_probe_client() -> Iterator[httpx.Client]:
    """Yield an allowlist-enforcing HTTP client for probes.

    Defined at module scope so tests can override it
    (``app.dependency_overrides[get_probe_client]``) with a client backed by a
    mock transport, keeping unit/integration tests off the network.
    """
    with build_probe_client(load_allowlist()) as client:
        yield client


def get_browser_runner() -> BrowserRunner:
    """Yield the FR-14 browser probe runner, bound to the allowlist guard.

    Overridable in tests with a stand-in runner that returns canned evidence, so
    the browser-probe execution path is tested without launching a real browser.
    """
    return make_browser_runner(load_allowlist())


def get_settings_dep(request: Request) -> Settings:
    """Load the persisted model/provider setting, seeding a fresh DB (ADR-0021)."""
    sessions = cast("sessionmaker[Session]", request.app.state.sessions)
    with sessions() as session:
        return load_or_seed(session)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_plan_agent(settings: SettingsDep) -> Agent[None, list[PlannedAction]]:
    """Yield the FR-04 plan agent built from the persisted setting (ADR-0021)."""
    return build_plan_agent(build_model(settings))


def get_extraction_agent(settings: SettingsDep) -> Agent[None, list[ExtractedFinding]]:
    """Yield the FR-03 extraction agent built from the persisted setting (ADR-0021)."""
    return build_extraction_agent(build_model(settings))


# Both underlying dependencies (get_probe_client, get_plan_agent) are module-level
# functions with no per-app-instance state, so these aliases can live here too —
# unlike SessionDep, which closes over each app's own session factory.
ProbeClientDep = Annotated[httpx.Client, Depends(get_probe_client)]
BrowserRunnerDep = Annotated[BrowserRunner, Depends(get_browser_runner)]
PlanAgentDep = Annotated[Agent[None, list[PlannedAction]], Depends(get_plan_agent)]
ExtractionAgentDep = Annotated[Agent[None, list[ExtractedFinding]], Depends(get_extraction_agent)]


def run_extraction(
    sessions: sessionmaker[Session],
    report_id: int,
    data: bytes,
    agent: Agent[None, list[ExtractedFinding]],
) -> None:
    """Extract findings from an uploaded PDF and persist them (FR-01/FR-03/FR-11).

    Runs as a FastAPI background task — a sync function Starlette dispatches to
    its threadpool, so it must open its **own** session (the request session is
    already closed once the ``202`` was sent) and it never blocks the event
    loop. The report is always moved out of ``extracting``: to ``ready`` with
    its findings persisted, or ``failed`` with the error recorded — so the UI's
    status poll always terminates.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        report_id: The ``extracting`` report row to fill in.
        data: The uploaded PDF bytes.
        agent: The extraction agent (a stand-in model in tests).
    """
    with sessions() as session:
        report = session.get(ReportRecord, report_id)
        if report is None:  # pragma: no cover - the row was just committed
            return
        try:
            result = extract_report(agent, read_pdf(data))
        except PdfError as exc:
            report.status, report.error = ReportStatus.FAILED.value, str(exc)
            session.commit()
            return
        except Exception as exc:
            report.status, report.error = ReportStatus.FAILED.value, f"extraction failed: {exc}"
            session.commit()
            return
        _persist_findings(session, result.findings, report_id=report_id)
        report.status = ReportStatus.READY.value
        report.finding_count = len(result.findings)
        session.commit()


def _get_finding_or_404(session: Session, finding_id: int) -> FindingRecord:
    """Return the finding identity row, or raise the shared 404 (FR-05 endpoints)."""
    finding = session.get(FindingRecord, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


def _current_or_404(session: Session, finding_id: int) -> FindingVersionRecord:
    """Return the finding's current version, or raise the shared 404 (FR-16).

    A finding always has a version (created together), so a missing current
    version means the finding itself is absent.
    """
    version = current_version(session, finding_id)
    if version is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return version


def _finding_out(identity: FindingRecord, version: FindingVersionRecord) -> FindingOut:
    """Assemble the API finding view from its identity + current version (FR-16)."""
    return FindingOut(
        id=identity.id,
        report_id=identity.report_id,
        version=version.version,
        **version.to_domain().model_dump(),
    )


def _persist_findings(
    session: Session, findings: Iterable[Finding], report_id: int | None = None
) -> None:
    """Create identity + version-1 rows for a batch of newly ingested findings (FR-16).

    Shared by extraction, FR-02 import, and manual entry — each lands every finding
    as its ``extraction`` version 1 (ADR-0024). The caller owns the ``commit``.
    """
    for finding in findings:
        create_finding(session, finding, report_id=report_id)


def run_plan_generation(
    sessions: sessionmaker[Session],
    finding_id: int,
    plan_id: int,
    agent: Agent[None, list[PlannedAction]],
    instructions: str = "",
) -> None:
    """Generate the reserved plan version's actions and settle it (FR-04, ADR-0022).

    Runs as a FastAPI background task — a sync function on Starlette's threadpool
    — so it opens its **own** session (the request session closed with the
    ``202``) and never blocks the event loop. It always moves the ``generating``
    row on: to ``proposed`` with its gated actions, or ``failed`` with the
    reason, so the SPA's plan poll terminates exactly like the report poll does.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        finding_id: The finding being planned.
        plan_id: The reserved ``generating`` version to fill in.
        agent: The plan agent (a stand-in model in tests).
        instructions: Optional operator guidance for this generation (ADR-0023).
    """
    with sessions() as session:
        version = current_version(session, finding_id)
        if version is None:  # pragma: no cover - the row was just committed
            return
        try:
            result = generate_plan(
                agent, version.to_domain(), load_allowlist(), lab_base_url(), instructions
            )
        except Exception as exc:
            result = PlanResult(
                plan=RetestPlan(finding_title=version.title),
                error=f"plan generation failed: {exc}",
            )
        finish_plan_generation(session, plan_id, result)


def _edit_plan(session: Session, finding_id: int, actions: list[PlannedAction]) -> PlanOut:
    """Replace the plan with edited actions as a new proposed version (FR-05)."""
    finding = _current_or_404(session, finding_id)
    try:
        record, _ = edit_plan(
            session,
            finding_id,
            actions,
            load_allowlist(),
            lab_base_url(),
            finding_title=finding.title,
        )
    except AllActionsRejectedError as exc:
        raise HTTPException(
            status_code=422,
            detail="all edited actions were rejected by the allowlist/method gate",
        ) from exc
    return PlanOut.from_record(record)


def _approve_plan(session: Session, finding_id: int) -> PlanOut:
    """Approve the latest proposed plan version (FR-05)."""
    _get_finding_or_404(session, finding_id)
    try:
        return PlanOut.from_record(approve_plan(session, finding_id))
    except NoProposedPlanError as exc:
        raise HTTPException(status_code=409, detail="no proposed plan to approve") from exc


def _reject_plan(session: Session, finding_id: int) -> PlanOut:
    """Reject the latest proposed plan version (FR-05)."""
    _get_finding_or_404(session, finding_id)
    try:
        return PlanOut.from_record(reject_plan(session, finding_id))
    except NoProposedPlanError as exc:
        raise HTTPException(status_code=409, detail="no proposed plan to reject") from exc


def _revise_plan(session: Session, finding_id: int) -> PlanOut:
    """Un-approve the approved plan back into an editable proposed copy (ADR-0023)."""
    finding = _current_or_404(session, finding_id)
    try:
        return PlanOut.from_record(revise_plan(session, finding_id, finding_title=finding.title))
    except PlanNotApprovedError as exc:
        raise HTTPException(status_code=409, detail="no approved plan to revise") from exc


def _list_plans(session: Session, finding_id: int) -> list[PlanOut]:
    """List all plan versions for a finding (FR-05)."""
    _get_finding_or_404(session, finding_id)
    return [PlanOut.from_record(record) for record in list_plans(session, finding_id)]


def _retest_finding(
    session: Session,
    client: httpx.Client,
    finding_id: int,
    browser_runner: BrowserRunner,
) -> list[VerdictOut]:
    """Execute the finding's APPROVED plan and persist the verdicts (FR-05/FR-07/FR-09/FR-14)."""
    _get_finding_or_404(session, finding_id)
    try:
        records = execute_approved_plan(session, client, finding_id, browser_runner=browser_runner)
    except PlanNotApprovedError as exc:
        raise HTTPException(
            status_code=409, detail="no approved plan; approve one before retesting"
        ) from exc
    except PlanDeviationError as exc:
        raise HTTPException(
            status_code=409, detail="execution blocked: probe deviates from the approved plan"
        ) from exc
    except BrowserProbeUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return [
        VerdictOut(
            id=r.id,
            finding_id=r.finding_id,
            probe_kind=r.probe_kind,
            plan_version=r.plan_version,
            **r.to_domain().model_dump(),
        )
        for r in records
    ]


def _register_core_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register ``/health`` and the FR-02 finding import/list routes.

    Split from :func:`_register_plan_routes` purely to keep each
    registration function's route-closure count under the mccabe gate (each
    nested route def adds to its enclosing function's measured complexity).
    Each registration function binds its own ``SessionDep``, closing over this
    app's ``sessions`` factory, rather than sharing one across functions. Routes
    are registered on the ``/api`` router (FR-11): the SPA owns the root paths.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/health")
    def health() -> dict[str, str]:
        """Liveness check."""
        return {"status": "ok", "version": __version__}

    @router.post("/findings/import", response_model=ImportResult)
    def import_findings(payload: dict[str, Any], session: SessionDep) -> ImportResult:
        """Ingest a DefectDojo-style JSON export (FR-02)."""
        try:
            findings = map_defectdojo_export(payload)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _persist_findings(session, findings)
        session.commit()
        return ImportResult(imported=len(findings))

    @router.get("/findings", response_model=list[FindingOut])
    def list_findings(session: SessionDep, report_id: int | None = None) -> list[FindingOut]:
        """List persisted findings (current version each), optionally by report (FR-11)."""
        stmt = select(FindingRecord).order_by(FindingRecord.id)
        if report_id is not None:
            stmt = stmt.where(FindingRecord.report_id == report_id)
        out: list[FindingOut] = []
        for identity in session.scalars(stmt):
            version = current_version(session, identity.id)
            if version is None:  # pragma: no cover - a finding always has >=1 version
                continue
            out.append(_finding_out(identity, version))
        return out


def _register_finding_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-16 finding-revision (versioned edit) and annotation routes.

    Split from :func:`_register_core_routes` to keep each registration function's
    nested-route count under the mccabe gate (see that function's note). Editing a
    finding appends an immutable version; notes are an append-only, stage-tagged
    log — nothing here mutates or deletes prior history (ADR-0024).
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}", response_model=FindingOut)
    def edit_finding(finding_id: int, body: FindingEditIn, session: SessionDep) -> FindingOut:
        """Record an operator edit as a new immutable finding version (FR-16)."""
        current = _current_or_404(session, finding_id)
        identity = _get_finding_or_404(session, finding_id)
        version = add_version(session, finding_id, body.to_finding(current.raw), reason=body.reason)
        return _finding_out(identity, version)

    @router.get("/findings/{finding_id}/versions", response_model=list[FindingVersionOut])
    def get_finding_versions(finding_id: int, session: SessionDep) -> list[FindingVersionOut]:
        """List every version of a finding, oldest first — extraction = v1 (FR-16)."""
        _current_or_404(session, finding_id)
        return [FindingVersionOut.from_record(r) for r in list_versions(session, finding_id)]

    @router.post("/findings/{finding_id}/notes", response_model=NoteOut, status_code=201)
    def add_finding_note(finding_id: int, body: NoteIn, session: SessionDep) -> NoteOut:
        """Append a stage-tagged note to the finding's log (FR-16)."""
        _get_finding_or_404(session, finding_id)
        return NoteOut.from_record(add_note(session, finding_id, body.stage, body.body))

    @router.get("/findings/{finding_id}/notes", response_model=list[NoteOut])
    def get_finding_notes(finding_id: int, session: SessionDep) -> list[NoteOut]:
        """List a finding's notes, newest first (FR-16)."""
        _get_finding_or_404(session, finding_id)
        return [NoteOut.from_record(r) for r in list_notes(session, finding_id)]


def _register_report_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-01/FR-11 report-upload and status routes.

    Upload runs FR-01→FR-03 in a background task (:func:`run_extraction`); the
    two GET routes are the overview list and the poll target the SPA watches
    until the report settles on ``ready``/``failed``.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/reports", response_model=ReportOut, status_code=202)
    async def create_report(
        file: UploadFile,
        background: BackgroundTasks,
        session: SessionDep,
        agent: ExtractionAgentDep,
    ) -> ReportOut:
        """Accept a PDF report and schedule background extraction (FR-01/FR-03/FR-11)."""
        data = await file.read()
        if not data:
            raise HTTPException(status_code=422, detail="empty upload")
        report = ReportRecord(
            filename=file.filename or "report.pdf",
            status=ReportStatus.EXTRACTING.value,
            model=agent_model_name(agent),
            finding_count=0,
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        background.add_task(run_extraction, sessions, report.id, data, agent)
        return ReportOut.from_record(report)

    @router.post("/reports/manual", response_model=ReportOut, status_code=201)
    def create_manual_report(payload: dict[str, Any], session: SessionDep) -> ReportOut:
        """Create a report and its findings directly, bypassing LLM extraction.

        The human-entry escape hatch (no FR-03): when a model cannot reliably
        ingest a report — e.g. a large report on a small local backend — a person
        supplies the findings by form or JSON upload. Reuses the FR-02 DefectDojo
        mapping per finding, then lands the report ``ready`` with its findings
        attached, so the FR-04/FR-05 plan→approve→retest flow is identical to an
        extracted report's. ``payload`` is ``{"label": str, "findings": [...]}``.
        """
        try:
            findings = map_defectdojo_export(payload)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not findings:
            raise HTTPException(
                status_code=422, detail="a manual report needs at least one finding"
            )
        label = str(payload.get("label") or "").strip() or "Manual report"
        report = ReportRecord(
            filename=label,
            status=ReportStatus.READY.value,
            model="manual",
            finding_count=len(findings),
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        _persist_findings(session, findings, report_id=report.id)
        session.commit()
        session.refresh(report)
        return ReportOut.from_record(report)

    @router.get("/reports", response_model=list[ReportOut])
    def list_reports(session: SessionDep) -> list[ReportOut]:
        """List uploaded reports, newest first (FR-11 overview)."""
        records = session.scalars(select(ReportRecord).order_by(ReportRecord.id.desc()))
        return [ReportOut.from_record(r) for r in records]

    @router.get("/reports/{report_id}", response_model=ReportOut)
    def get_report(report_id: int, session: SessionDep) -> ReportOut:
        """Return one report's current status (the SPA's poll target, FR-11)."""
        report = session.get(ReportRecord, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return ReportOut.from_record(report)


def _register_plan_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-05 plan lifecycle routes (generate/edit/approve/reject/revise/list)."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}/plan", response_model=PlanOut, status_code=202)
    def create_plan(
        finding_id: int,
        background: BackgroundTasks,
        session: SessionDep,
        agent: PlanAgentDep,
        body: PlanRequest | None = None,
    ) -> PlanOut:
        """Schedule background FR-04 plan generation; persist a ``generating`` version (ADR-0022).

        Returns ``202`` immediately with the reserved version so a reload always
        shows the real in-progress state; :func:`run_plan_generation` settles it
        to ``proposed``/``failed`` and the SPA polls ``/plans`` until it does.
        This is also the *regenerate* path: it supersedes any live version — an
        approved one included — so the operator can start over at any point
        (ADR-0023). The optional ``instructions`` body steers this generation.
        """
        _get_finding_or_404(session, finding_id)
        instructions = body.instructions if body else ""
        record = start_plan_generation(session, finding_id, instructions)
        background.add_task(
            run_plan_generation, sessions, finding_id, record.id, agent, instructions
        )
        return PlanOut.from_record(record)

    @router.put("/findings/{finding_id}/plan", response_model=PlanOut)
    def edit_plan_endpoint(
        finding_id: int, actions: list[PlannedAction], session: SessionDep
    ) -> PlanOut:
        """Replace the plan with edited actions as a new proposed version (FR-05)."""
        return _edit_plan(session, finding_id, actions)

    @router.post("/findings/{finding_id}/plan/approve", response_model=PlanOut)
    def approve_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Approve the latest proposed plan version (FR-05)."""
        return _approve_plan(session, finding_id)

    @router.post("/findings/{finding_id}/plan/reject", response_model=PlanOut)
    def reject_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Reject the latest proposed plan version (FR-05)."""
        return _reject_plan(session, finding_id)

    @router.post("/findings/{finding_id}/plan/revise", response_model=PlanOut)
    def revise_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Un-approve the approved plan back into an editable proposed copy (ADR-0023)."""
        return _revise_plan(session, finding_id)

    @router.get("/findings/{finding_id}/plans", response_model=list[PlanOut])
    def get_plans(finding_id: int, session: SessionDep) -> list[PlanOut]:
        """List all plan versions for a finding (FR-05)."""
        return _list_plans(session, finding_id)


def _register_retest_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the plan-driven retest, verdict-list, and FR-10 audit routes.

    Split from :func:`_register_plan_routes` to keep each registration function's
    nested-route count under the mccabe gate (see :func:`_register_core_routes`).
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}/retest", response_model=list[VerdictOut])
    def retest_finding(
        finding_id: int,
        session: SessionDep,
        client: ProbeClientDep,
        browser_runner: BrowserRunnerDep,
    ) -> list[VerdictOut]:
        """Execute the finding's approved plan; persist the verdicts (FR-05/07/09/14)."""
        return _retest_finding(session, client, finding_id, browser_runner)

    @router.get("/verdicts", response_model=list[VerdictOut])
    def list_verdicts(session: SessionDep) -> list[VerdictOut]:
        """List all persisted verdicts (FR-09)."""
        records = session.scalars(select(VerdictRecord).order_by(VerdictRecord.id))
        return [
            VerdictOut(
                id=r.id,
                finding_id=r.finding_id,
                probe_kind=r.probe_kind,
                plan_version=r.plan_version,
                **r.to_domain().model_dump(),
            )
            for r in records
        ]

    @router.get("/audit", response_model=AuditOut)
    def audit_rederive(session: SessionDep) -> AuditOut:
        """Re-derive every verdict from stored evidence alone — no re-execution (FR-10)."""
        report = rederive_run(session)
        return AuditOut(
            total=report.total,
            reproduced=report.reproduced,
            ok=report.ok,
            discrepancies=[
                DiscrepancyOut(
                    verdict_id=d.verdict_id,
                    finding_id=d.finding_id,
                    stored=d.stored,
                    rederived=d.rederived,
                )
                for d in report.discrepancies
            ],
        )


def _register_export_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-12 versioned run-export routes.

    ``/export`` serves the whole run as one versioned JSON document (the FR-15
    evaluation harness consumes it); ``/export/schema`` serves the JSON Schema
    that document validates against.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/export", response_model=RunExport)
    def export_run(session: SessionDep) -> RunExport:
        """Export the complete run as a versioned JSON document (FR-12)."""
        return build_export(session)

    @router.get("/export/schema")
    def export_run_schema() -> dict[str, Any]:
        """Return the published JSON Schema the export validates against (FR-12)."""
        return export_schema()


def _register_settings_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the ADR-0021 model/provider settings routes."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/settings", response_model=SettingsOut)
    def read_settings(session: SessionDep) -> SettingsOut:
        """Return the current model/provider setting (key masked)."""
        return SettingsOut.from_domain(load_or_seed(session))

    @router.put("/settings", response_model=SettingsOut)
    def update_settings(body: SettingsUpdateIn, session: SessionDep) -> SettingsOut:
        """Persist a new model/provider setting; takes effect on the next agent build."""
        cfg = save(
            session,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
            clear_key=body.clear_key,
        )
        return SettingsOut.from_domain(cfg)

    @router.post("/settings/probe", response_model=ProbeResult)
    def probe_settings(body: ProbeIn) -> ProbeResult:
        """Discover models / test reachability for a provider base URL (ADR-0021)."""
        return probe_provider(body.base_url, body.api_key)


def _mount_spa(app: FastAPI, dist: Path = _SPA_DIST) -> None:
    """Serve the built SPA at ``/`` with a client-routing catch-all (FR-11).

    No-op when the build is absent (backend-only dev, CI unit runs) — the API
    still works. Registered after the ``/api`` router so it never shadows it;
    the catch-all serves any real built file and otherwise returns
    ``index.html`` so deep links (``/findings/5``) reload into the SPA. Binds
    nothing new: the app still listens only on ``127.0.0.1`` (NFR-03).
    """
    index = dist / "index.html"
    if not index.is_file():
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api"):
            raise HTTPException(status_code=404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(db_path: str = "revalid.db", engine: Engine | None = None) -> FastAPI:
    """Build the application with its own database engine.

    Args:
        db_path: SQLite file backing this instance; ignored when ``engine``
            is given (tests inject an in-memory engine).
        engine: Pre-built engine to use instead of opening ``db_path``.

    Returns:
        The configured FastAPI application.
    """
    db_engine = engine if engine is not None else create_db_engine(db_path)
    sessions = session_factory(db_engine)
    app = FastAPI(title="revalid", version=__version__)
    app.state.sessions = sessions

    api = APIRouter(prefix="/api")
    _register_core_routes(api, sessions)
    _register_finding_routes(api, sessions)
    _register_report_routes(api, sessions)
    _register_plan_routes(api, sessions)
    _register_retest_routes(api, sessions)
    _register_export_routes(api, sessions)
    _register_settings_routes(api, sessions)
    app.include_router(api)
    _mount_spa(app)

    return app
