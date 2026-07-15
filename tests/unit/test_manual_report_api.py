"""Unit tests for the manual report-creation endpoint (bypasses LLM ingestion).

The human-entry escape hatch: a person supplies findings by form or JSON upload,
and the report lands ``ready`` with its findings attached — no model involved.
"""

from typing import Any

from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import IN_MEMORY, create_db_engine

_PAYLOAD: dict[str, Any] = {
    "label": "Manual pentest",
    "findings": [
        {
            "title": "IDOR: access another basket",
            "severity": "medium",
            "description": "Predictable basket id, no ownership check.",
            "endpoints": ["/rest/basket/2"],
            "steps_to_reproduce": "1. Log in\n2. Increment the id",
        },
        {"title": "SQL injection in login", "severity": "high"},
    ],
}


def _client() -> TestClient:
    return TestClient(create_app(engine=create_db_engine(IN_MEMORY)))


def test_create_manual_report_lands_ready_with_findings() -> None:
    with _client() as client:
        response = client.post("/api/reports/manual", json=_PAYLOAD)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "ready"
        assert body["model"] == "manual"
        assert body["finding_count"] == 2

        report_id = body["id"]
        findings = client.get(f"/api/findings?report_id={report_id}").json()
        assert [f["title"] for f in findings] == [
            "IDOR: access another basket",
            "SQL injection in login",
        ]
        assert findings[0]["affected_endpoints"] == ["/rest/basket/2"]
        assert findings[0]["reproduction_steps"] == ["1. Log in", "2. Increment the id"]
        # The manual report is visible in the overview list (attached, not orphaned).
        assert any(r["id"] == report_id for r in client.get("/api/reports").json())


def test_manual_report_requires_at_least_one_finding() -> None:
    with _client() as client:
        response = client.post("/api/reports/manual", json={"label": "x", "findings": []})
        assert response.status_code == 422


def test_manual_report_rejects_missing_findings_array() -> None:
    with _client() as client:
        response = client.post("/api/reports/manual", json={"label": "x"})
        assert response.status_code == 422


def test_manual_report_defaults_label_and_maps_severity_alias() -> None:
    with _client() as client:
        response = client.post(
            "/api/reports/manual",
            json={"findings": [{"title": "T", "severity": "Critical"}]},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "Manual report"  # blank label -> default
        findings = client.get(f"/api/findings?report_id={body['id']}").json()
        assert findings[0]["severity"] == "critical"  # case-insensitive alias
