"""FastAPI application factory (ADR-0002: local single-user web app).

Run locally with::

    uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1

The app must only ever bind to 127.0.0.1 (NFR-03); there is no
authentication in TFG scope.
"""

from collections.abc import Iterator
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from revalid import __version__
from revalid.allowlist import load_allowlist
from revalid.db import FindingRecord, VerdictRecord, create_db_engine, session_factory
from revalid.domain import Finding, Verdict
from revalid.ingest import IngestError, map_defectdojo_export
from revalid.retest import build_probe_client, lab_base_url, login_sqli_probe, run_probe


class FindingOut(Finding):
    """A persisted finding as returned by the API (domain model + row id)."""

    id: int


class VerdictOut(Verdict):
    """A persisted verdict as returned by the API (domain model + linkage)."""

    id: int
    finding_id: int
    probe_kind: str


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

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806
    ProbeClientDep = Annotated[httpx.Client, Depends(get_probe_client)]  # noqa: N806

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

    @app.post("/findings/{finding_id}/retest", response_model=VerdictOut)
    def retest_finding(finding_id: int, session: SessionDep, client: ProbeClientDep) -> VerdictOut:
        """Run the retest probe for a finding and persist the verdict (FR-07/FR-09)."""
        if session.get(FindingRecord, finding_id) is None:
            raise HTTPException(status_code=404, detail="finding not found")
        probe = login_sqli_probe(lab_base_url())
        verdict = run_probe(client, probe)
        record = VerdictRecord.from_domain(finding_id, probe.kind, verdict)
        session.add(record)
        session.commit()
        session.refresh(record)
        return VerdictOut(
            id=record.id, finding_id=finding_id, probe_kind=probe.kind, **verdict.model_dump()
        )

    @app.get("/verdicts", response_model=list[VerdictOut])
    def list_verdicts(session: SessionDep) -> list[VerdictOut]:
        """List all persisted verdicts (FR-09)."""
        records = session.scalars(select(VerdictRecord).order_by(VerdictRecord.id))
        return [
            VerdictOut(
                id=r.id,
                finding_id=r.finding_id,
                probe_kind=r.probe_kind,
                **r.to_domain().model_dump(),
            )
            for r in records
        ]

    return app
