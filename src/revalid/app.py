"""FastAPI application factory (ADR-0002: local single-user web app).

Run locally with::

    uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1

The app must only ever bind to 127.0.0.1 (NFR-03); there is no
authentication in TFG scope.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from pydantic_ai import Agent
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from revalid import __version__
from revalid.allowlist import load_allowlist
from revalid.approval import (
    AllActionsRejectedError,
    NoProposedPlanError,
    PlanNotApprovedError,
    approve_plan,
    edit_plan,
    execute_approved_plan,
    list_plans,
    reject_plan,
    save_generated_plan,
)
from revalid.db import FindingRecord, PlanRecord, VerdictRecord, create_db_engine, session_factory
from revalid.domain import Finding, Probe, Verdict
from revalid.ingest import IngestError, map_defectdojo_export
from revalid.plan import PlannedAction, RejectedAction, build_plan_agent, generate_plan
from revalid.retest import build_probe_client, lab_base_url


class FindingOut(Finding):
    """A persisted finding as returned by the API (domain model + row id)."""

    id: int


class VerdictOut(Verdict):
    """A persisted verdict as returned by the API (domain model + linkage)."""

    id: int
    finding_id: int
    probe_kind: str
    plan_version: int | None = None


class PlanOut(BaseModel):
    """A persisted retest-plan version as returned by the API (FR-05)."""

    id: int
    finding_id: int
    version: int
    status: str
    origin: str
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


def get_probe_client() -> Iterator[httpx.Client]:
    """Yield an allowlist-enforcing HTTP client for probes.

    Defined at module scope so tests can override it
    (``app.dependency_overrides[get_probe_client]``) with a client backed by a
    mock transport, keeping unit/integration tests off the network.
    """
    with build_probe_client(load_allowlist()) as client:
        yield client


def get_plan_agent() -> Agent[None, list[PlannedAction]]:
    """Yield the FR-04 plan agent (overridden in tests with a stand-in model)."""
    return build_plan_agent()


# Both underlying dependencies (get_probe_client, get_plan_agent) are module-level
# functions with no per-app-instance state, so these aliases can live here too —
# unlike SessionDep, which closes over each app's own session factory.
ProbeClientDep = Annotated[httpx.Client, Depends(get_probe_client)]
PlanAgentDep = Annotated[Agent[None, list[PlannedAction]], Depends(get_plan_agent)]


def _get_finding_or_404(session: Session, finding_id: int) -> FindingRecord:
    """Return the finding row, or raise the shared 404 (FR-05 endpoints)."""
    finding = session.get(FindingRecord, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


def _generate_plan(
    session: Session, finding_id: int, agent: Agent[None, list[PlannedAction]]
) -> PlanOut:
    """Generate a retest plan (FR-04) and persist it as a proposed version (FR-05)."""
    finding = _get_finding_or_404(session, finding_id)
    result = generate_plan(agent, finding.to_domain(), load_allowlist(), lab_base_url())
    if result.error:
        raise HTTPException(status_code=422, detail=f"plan generation failed: {result.error}")
    if not result.plan.actions:
        raise HTTPException(
            status_code=422, detail="no runnable actions could be planned for this finding"
        )
    return PlanOut.from_record(save_generated_plan(session, finding_id, result))


def _edit_plan(session: Session, finding_id: int, actions: list[PlannedAction]) -> PlanOut:
    """Replace the plan with edited actions as a new proposed version (FR-05)."""
    finding = _get_finding_or_404(session, finding_id)
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


def _list_plans(session: Session, finding_id: int) -> list[PlanOut]:
    """List all plan versions for a finding (FR-05)."""
    _get_finding_or_404(session, finding_id)
    return [PlanOut.from_record(record) for record in list_plans(session, finding_id)]


def _retest_finding(session: Session, client: httpx.Client, finding_id: int) -> list[VerdictOut]:
    """Execute the finding's APPROVED plan and persist the verdicts (FR-05/FR-07/FR-09)."""
    _get_finding_or_404(session, finding_id)
    try:
        records = execute_approved_plan(session, client, finding_id)
    except PlanNotApprovedError as exc:
        raise HTTPException(
            status_code=409, detail="no approved plan; approve one before retesting"
        ) from exc
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


def _register_core_routes(app: FastAPI, sessions: sessionmaker[Session]) -> None:
    """Register ``/health`` and the FR-02 finding import/list routes.

    Split from :func:`_register_plan_and_retest_routes` purely to keep each
    registration function's route-closure count under the mccabe gate (each
    nested route def adds to its enclosing function's measured complexity).
    Each registration function binds its own ``SessionDep``, closing over this
    app's ``sessions`` factory, rather than sharing one across functions.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness check."""
        return {"status": "ok", "version": __version__}

    @app.post("/findings/import", response_model=ImportResult)
    def import_findings(payload: dict[str, Any], session: SessionDep) -> ImportResult:
        """Ingest a DefectDojo-style JSON export (FR-02)."""
        try:
            findings = map_defectdojo_export(payload)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.add_all(FindingRecord.from_domain(f) for f in findings)
        session.commit()
        return ImportResult(imported=len(findings))

    @app.get("/findings", response_model=list[FindingOut])
    def list_findings(session: SessionDep) -> list[FindingOut]:
        """List all persisted findings."""
        records = session.scalars(select(FindingRecord).order_by(FindingRecord.id))
        return [FindingOut(id=r.id, **r.to_domain().model_dump()) for r in records]


def _register_plan_and_retest_routes(app: FastAPI, sessions: sessionmaker[Session]) -> None:
    """Register the FR-05 plan lifecycle routes and the plan-driven retest/verdicts routes."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @app.post("/findings/{finding_id}/plan", response_model=PlanOut)
    def create_plan(finding_id: int, session: SessionDep, agent: PlanAgentDep) -> PlanOut:
        """Generate a retest plan (FR-04) and persist it as a proposed version (FR-05)."""
        return _generate_plan(session, finding_id, agent)

    @app.put("/findings/{finding_id}/plan", response_model=PlanOut)
    def edit_plan_endpoint(
        finding_id: int, actions: list[PlannedAction], session: SessionDep
    ) -> PlanOut:
        """Replace the plan with edited actions as a new proposed version (FR-05)."""
        return _edit_plan(session, finding_id, actions)

    @app.post("/findings/{finding_id}/plan/approve", response_model=PlanOut)
    def approve_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Approve the latest proposed plan version (FR-05)."""
        return _approve_plan(session, finding_id)

    @app.post("/findings/{finding_id}/plan/reject", response_model=PlanOut)
    def reject_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Reject the latest proposed plan version (FR-05)."""
        return _reject_plan(session, finding_id)

    @app.get("/findings/{finding_id}/plans", response_model=list[PlanOut])
    def get_plans(finding_id: int, session: SessionDep) -> list[PlanOut]:
        """List all plan versions for a finding (FR-05)."""
        return _list_plans(session, finding_id)

    @app.post("/findings/{finding_id}/retest", response_model=list[VerdictOut])
    def retest_finding(
        finding_id: int, session: SessionDep, client: ProbeClientDep
    ) -> list[VerdictOut]:
        """Execute the finding's APPROVED plan and persist the verdicts (FR-05/FR-07/FR-09)."""
        return _retest_finding(session, client, finding_id)

    @app.get("/verdicts", response_model=list[VerdictOut])
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

    _register_core_routes(app, sessions)
    _register_plan_and_retest_routes(app, sessions)

    return app
