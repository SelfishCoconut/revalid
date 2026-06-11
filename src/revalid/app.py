"""FastAPI application factory (ADR-0002: local single-user web app).

Run locally with::

    uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1

The app must only ever bind to 127.0.0.1 (NFR-03); there is no
authentication in TFG scope.
"""

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from revalid import __version__
from revalid.db import FindingRecord, create_db_engine, session_factory
from revalid.domain import Finding
from revalid.ingest import IngestError, map_defectdojo_export


class FindingOut(Finding):
    """A persisted finding as returned by the API (domain model + row id)."""

    id: int


class ImportResult(BaseModel):
    """Outcome of a findings import."""

    imported: int


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

    return app
