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

from revalid.app import create_app, get_extraction_agent
from revalid.db import IN_MEMORY, create_db_engine
from revalid.extract import ExtractedFinding

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"


def test_ingest_operable_over_api(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_extraction_agent] = lambda: extraction_agent
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
