"""Integration test: the FR-11 ingest path is operable end-to-end over /api.

Drives upload → extract (FR-01/FR-03) → finding listed entirely through the
``/api`` surface, wiring the real components with a deterministic extraction
stand-in — the automated form of "operable from the UI alone", no network, no
lab. (Retest is the FR-17 agentic console, covered by ``test_retest_session_api``.)
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import Agent

from revalid.app import create_app, get_extraction_agent, get_metadata_agent
from revalid.db import IN_MEMORY, ReportRecord, create_db_engine, session_factory
from revalid.domain import ReportStatus
from revalid.extract import ExtractedFinding, ReportMetadata

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"


def test_ingest_operable_over_api(
    extraction_agent: Agent[None, list[ExtractedFinding]],
    metadata_agent: Agent[None, ReportMetadata],
) -> None:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_extraction_agent] = lambda: extraction_agent
    app.dependency_overrides[get_metadata_agent] = lambda: metadata_agent
    client = TestClient(app)

    # 1. ingest: the upload's background extraction runs before the response returns.
    upload = client.post(
        "/api/reports",
        files={"file": ("report.pdf", FIXTURE.read_bytes(), "application/pdf")},
    )
    assert upload.status_code == 202
    report_id = upload.json()["id"]
    assert client.get(f"/api/reports/{report_id}").json()["status"] == "ready"

    # 2. a finding from that report is now listed over /api.
    findings = client.get("/api/findings", params={"report_id": report_id}).json()
    assert findings and findings[0]["id"]


def test_startup_fails_reports_orphaned_in_extracting() -> None:
    """A report stuck in 'extracting' by a restart is failed on next startup (#131).

    Background extraction is in-memory, so a crash/restart would otherwise leave
    the row in 'extracting' forever. Rebuilding the app over the same engine must
    settle it to 'failed' so the UI's status poll always terminates.
    """
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as session:
        session.add(
            ReportRecord(
                filename="stuck.pdf",
                status=ReportStatus.EXTRACTING.value,
                model="stand-in",
                finding_count=0,
            )
        )
        session.commit()

    app = create_app(engine=engine)  # startup reconcile runs here
    client = TestClient(app)

    reports = client.get("/api/reports").json()
    assert reports[0]["status"] == "failed"
    assert "interrupted" in reports[0]["error"]


def test_duplicate_upload_is_rejected_until_forced(
    extraction_agent: Agent[None, list[ExtractedFinding]],
    metadata_agent: Agent[None, ReportMetadata],
) -> None:
    """Re-uploading identical bytes returns 409 with the matches; force re-ingests (#134)."""
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_extraction_agent] = lambda: extraction_agent
    app.dependency_overrides[get_metadata_agent] = lambda: metadata_agent
    client = TestClient(app)
    pdf = FIXTURE.read_bytes()

    first = client.post("/api/reports", files={"file": ("r.pdf", pdf, "application/pdf")})
    assert first.status_code == 202
    assert len(first.json()["content_hash"]) == 64

    dup = client.post("/api/reports", files={"file": ("r.pdf", pdf, "application/pdf")})
    assert dup.status_code == 409
    duplicates = dup.json()["detail"]["duplicates"]
    assert [d["id"] for d in duplicates] == [first.json()["id"]]

    forced = client.post(
        "/api/reports", params={"force": "true"}, files={"file": ("r.pdf", pdf, "application/pdf")}
    )
    assert forced.status_code == 202
    assert forced.json()["id"] != first.json()["id"]


def test_report_metadata_can_be_edited() -> None:
    """Operator edits to a report's document metadata persist and round-trip (#133)."""
    client = TestClient(create_app(engine=create_db_engine(IN_MEMORY)))
    created = client.post(
        "/api/reports/manual",
        json={"label": "T", "findings": [{"title": "x", "severity": "high", "description": "d"}]},
    )
    report_id = created.json()["id"]
    assert created.json()["metadata"] is None  # a manual report starts without metadata

    meta = {
        "product": "Juice Shop",
        "report_date": "2026-07-19",
        "author": "A. Navarro",
        "people": [{"name": "A. Navarro", "role": "pentester"}],
    }
    resp = client.put(f"/api/reports/{report_id}/metadata", json=meta)
    assert resp.status_code == 200
    assert resp.json()["metadata"] == meta
    assert client.get(f"/api/reports/{report_id}").json()["metadata"] == meta


def test_archive_hides_report_and_delete_cascades() -> None:
    """Archiving soft-hides a report; deleting removes it and its findings (#128)."""
    app = create_app(engine=create_db_engine(IN_MEMORY))
    client = TestClient(app)
    created = client.post(
        "/api/reports/manual",
        json={"label": "T", "findings": [{"title": "x", "severity": "high", "description": "d"}]},
    )
    report_id = created.json()["id"]
    assert created.json()["archived"] is False

    # archive: leaves the active list, appears in the archived list (reversible).
    assert client.patch(f"/api/reports/{report_id}", json={"archived": True}).status_code == 200
    assert client.get("/api/reports").json() == []
    archived = client.get("/api/reports", params={"archived": True}).json()
    assert [r["id"] for r in archived] == [report_id]

    # delete an archived report: gone from both lists, findings cascaded away.
    assert client.delete(f"/api/reports/{report_id}").status_code == 204
    assert client.get("/api/reports", params={"archived": True}).json() == []
    assert client.get("/api/findings", params={"report_id": report_id}).json() == []
    assert client.get(f"/api/reports/{report_id}").status_code == 404
