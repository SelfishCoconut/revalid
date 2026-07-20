"""Unit tests for the FR-11 report-ingest endpoints and background worker.

TestClient + in-memory SQLite + the deterministic ``extraction_agent`` fixture,
so no model is called. Starlette runs the upload's background task before the
``TestClient`` returns, so the report is deterministically settled (``ready`` /
``failed``) by the time the POST responds — no polling or sleeping in tests.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

from revalid import app as app_module
from revalid.app import create_app, get_extraction_agent, get_metadata_agent, run_extraction
from revalid.db import (
    IN_MEMORY,
    FindingRecord,
    ReportRecord,
    create_db_engine,
    session_factory,
)
from revalid.domain import ReportStatus
from revalid.extract import ExtractedFinding, ReportMetadata, build_metadata_agent

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"


def _client(agent: Agent[None, list[ExtractedFinding]]) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_extraction_agent] = lambda: agent
    app.dependency_overrides[get_metadata_agent] = lambda: build_metadata_agent(TestModel())
    return TestClient(app)


def _upload(client: TestClient, data: bytes, name: str = "report.pdf") -> Any:
    return client.post("/api/reports", files={"file": (name, data, "application/pdf")})


def test_upload_extracts_and_persists_findings(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    client = _client(extraction_agent)

    upload = _upload(client, FIXTURE.read_bytes())
    assert upload.status_code == 202
    assert upload.json()["status"] == "extracting"
    report_id = upload.json()["id"]

    report = client.get(f"/api/reports/{report_id}").json()
    assert report["status"] == "ready"
    assert report["finding_count"] == 4

    findings = client.get("/api/findings", params={"report_id": report_id}).json()
    assert len(findings) == 4
    assert all(f["report_id"] == report_id for f in findings)


def test_reports_overview_lists_newest_first(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    client = _client(extraction_agent)
    _upload(client, FIXTURE.read_bytes(), name="a.pdf")
    # Same bytes again → forced past the #134 duplicate guard for the ordering check.
    client.post(
        "/api/reports",
        params={"force": "true"},
        files={"file": ("b.pdf", FIXTURE.read_bytes(), "application/pdf")},
    )

    reports = client.get("/api/reports").json()
    assert [r["filename"] for r in reports] == ["b.pdf", "a.pdf"]
    assert all(r["status"] == "ready" for r in reports)


def test_upload_non_pdf_is_marked_failed(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    client = _client(extraction_agent)
    upload = _upload(client, b"this is not a pdf", name="bad.pdf")
    assert upload.status_code == 202

    report = client.get(f"/api/reports/{upload.json()['id']}").json()
    assert report["status"] == "failed"
    assert report["error"]
    assert report["finding_count"] == 0
    assert client.get("/api/findings").json() == []


def test_empty_upload_is_rejected(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    assert _upload(_client(extraction_agent), b"").status_code == 422


def test_unknown_report_is_404(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    assert _client(extraction_agent).get("/api/reports/999").status_code == 404


def test_run_extraction_records_unexpected_error(
    extraction_agent: Agent[None, list[ExtractedFinding]],
    metadata_agent: Agent[None, ReportMetadata],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected error never leaves a report stuck in ``extracting``."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        report = ReportRecord(
            filename="r.pdf",
            status=ReportStatus.EXTRACTING.value,
            model="test",
            finding_count=0,
        )
        session.add(report)
        session.commit()
        report_id = report.id

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(app_module, "extract_report", boom)
    run_extraction(sessions, report_id, FIXTURE.read_bytes(), extraction_agent, metadata_agent)

    with sessions() as session:
        settled = session.get(ReportRecord, report_id)
        assert settled is not None
        assert settled.status == ReportStatus.FAILED.value
        assert "extraction failed" in (settled.error or "")
        assert session.scalars(select(FindingRecord)).all() == []
