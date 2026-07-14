"""Unit tests for the API slice using an in-memory database (no file/network I/O)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import IN_MEMORY, create_db_engine

SAMPLE_EXPORT: dict[str, object] = {
    "scan_type": "Manual pentest",
    "findings": [
        {
            "title": "SQL injection in product search",
            "severity": "Critical",
            "description": "Injectable q parameter.",
            "steps_to_reproduce": "1. search\n2. inject",
            "endpoints": ["http://localhost:3000/rest/products/search"],
            "cwe": 89,
        },
        {"title": "Verbose error page", "severity": "Low"},
    ],
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_import_then_list_roundtrip(client: TestClient) -> None:
    response = client.post("/api/findings/import", json=SAMPLE_EXPORT)
    assert response.status_code == 200
    assert response.json() == {"imported": 2}

    listed = client.get("/api/findings").json()
    assert [f["title"] for f in listed] == [
        "SQL injection in product search",
        "Verbose error page",
    ]
    first = listed[0]
    assert first["id"] == 1
    assert first["severity"] == "critical"
    assert first["reproduction_steps"] == ["1. search", "2. inject"]
    assert first["raw"]["cwe"] == 89


def test_import_invalid_export_is_422_and_nothing_persisted(client: TestClient) -> None:
    bad = {"findings": [{"title": "ok", "severity": "Low"}, {"title": "no severity"}]}
    response = client.post("/api/findings/import", json=bad)
    assert response.status_code == 422
    assert "severity" in response.json()["detail"]
    assert client.get("/api/findings").json() == []


def test_list_empty_initially(client: TestClient) -> None:
    assert client.get("/api/findings").json() == []
