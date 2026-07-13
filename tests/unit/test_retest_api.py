"""Unit tests for the retest API endpoints with a mocked probe client (no network).

The probe client dependency is overridden with an ``httpx.MockTransport`` so the
endpoints are exercised end-to-end (ingest -> retest -> verdict) off the network.
"""

from collections.abc import Callable, Iterator

import httpx
from fastapi.testclient import TestClient

from revalid.app import create_app, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine

FINDING_EXPORT: dict[str, object] = {
    "scan_type": "Manual pentest",
    "findings": [{"title": "SQL injection auth bypass in login", "severity": "Critical"}],
}

Handler = Callable[[httpx.Request], httpx.Response]


def _make_client(handler: Handler) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))

    def override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = override
    return TestClient(app)


def _token_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def test_retest_still_open_persists_and_links_verdict() -> None:
    with _make_client(_token_response) as client:
        client.post("/findings/import", json=FINDING_EXPORT)

        response = client.post("/findings/1/retest")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "still_open"
        assert body["reason_code"] == "sqli_auth_bypass_succeeded"
        assert body["finding_id"] == 1
        assert body["probe_kind"] == "sqli-login-bypass"
        assert body["evidence"]["response_status"] == 200

        verdicts = client.get("/verdicts").json()
        assert len(verdicts) == 1
        assert verdicts[0]["finding_id"] == 1
        assert verdicts[0]["evidence"]["request_url"].endswith("/rest/user/login")


def test_retest_fixed_when_login_rejected() -> None:
    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid email or password."})

    with _make_client(rejected) as client:
        client.post("/findings/import", json=FINDING_EXPORT)
        assert client.post("/findings/1/retest").json()["status"] == "fixed"


def test_retest_unknown_finding_is_404() -> None:
    with _make_client(_token_response) as client:
        assert client.post("/findings/999/retest").status_code == 404


def test_verdicts_empty_initially() -> None:
    with _make_client(_token_response) as client:
        assert client.get("/verdicts").json() == []
