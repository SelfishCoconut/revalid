"""Unit tests for the rewired retest endpoint: execution requires approval (FR-05).

The probe client is an ``httpx.MockTransport`` and the plan agent is a
``FunctionModel``, so the flow runs off-network. A *generated* action for the
SQLi-login finding is classified as ``sqli-login-bypass`` (ADR-0019) and, given
the mock's token response, assesses as a conclusive ``still_open``.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_plan_agent, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine
from revalid.domain import Probe
from revalid.plan import PlannedAction, build_plan_agent
from revalid.sanity import PlanDeviationError

FINDING_EXPORT: dict[str, object] = {
    "scan_type": "Manual pentest",
    "findings": [
        {
            "title": "SQL injection auth bypass in login",
            "severity": "Critical",
            "endpoints": ["http://localhost:3000/rest/user/login"],
        }
    ],
}

_SQLI_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with an authentication token means still open.",
}

Handler = Callable[[httpx.Request], httpx.Response]


def _agent() -> Agent[None, list[PlannedAction]]:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": [_SQLI_ACTION]})]
        )

    return build_plan_agent(FunctionModel(respond))


def _make_client(handler: Handler) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))

    def override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = override
    app.dependency_overrides[get_plan_agent] = _agent
    return TestClient(app)


def _token_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def _approve(client: TestClient) -> None:
    client.post("/api/findings/import", json=FINDING_EXPORT)
    client.post("/api/findings/1/plan")
    client.post("/api/findings/1/plan/approve")


def test_retest_requires_approval() -> None:
    with _make_client(_token_response) as client:
        client.post("/api/findings/import", json=FINDING_EXPORT)
        client.post("/api/findings/1/plan")
        assert client.post("/api/findings/1/retest").status_code == 409  # AC1


def test_retest_executes_approved_plan_and_stamps_version() -> None:
    with _make_client(_token_response) as client:
        _approve(client)
        verdicts = client.post("/api/findings/1/retest").json()
        # The generated action for the SQLi-login finding is classified as
        # sqli-login-bypass (ADR-0019); the mock returns a token, so the verdict is
        # a conclusive still_open — the chokepoint ran it and stamped v1.
        assert verdicts[0]["status"] == "still_open"
        assert verdicts[0]["reason_code"] == "sqli_auth_bypass_succeeded"
        assert verdicts[0]["plan_version"] == 1
        assert verdicts[0]["evidence"]["request_url"].endswith("/rest/user/login")

        listed = client.get("/api/verdicts").json()
        assert listed[0]["plan_version"] == 1


def test_retest_blocks_a_plan_deviation_with_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-08 AC1 at the API: a blocked plan deviation surfaces as 409, not a 500."""

    def deviate(*_args: object, **_kwargs: object) -> list[object]:
        raise PlanDeviationError(
            Probe(kind="planned-http", method="GET", url="http://x/rest/admin")
        )

    monkeypatch.setattr("revalid.app.execute_approved_plan", deviate)
    with _make_client(_token_response) as client:
        _approve(client)
        response = client.post("/api/findings/1/retest")
        assert response.status_code == 409
        assert "deviat" in response.json()["detail"].lower()


def test_retest_unknown_finding_is_404() -> None:
    with _make_client(_token_response) as client:
        assert client.post("/api/findings/999/retest").status_code == 404


def test_verdicts_empty_initially() -> None:
    with _make_client(_token_response) as client:
        assert client.get("/api/verdicts").json() == []


def test_audit_endpoint_rederives_all_verdicts() -> None:
    """FR-10: GET /api/audit reproduces every verdict from stored evidence."""
    with _make_client(_token_response) as client:
        _approve(client)
        client.post("/api/findings/1/retest")
        audit = client.get("/api/audit").json()
        assert audit["total"] >= 1
        assert audit["reproduced"] == audit["total"]
        assert audit["ok"] is True
        assert audit["discrepancies"] == []


def _app_with_browser_plan() -> FastAPI:
    """Build an app whose shared engine holds an approved browser-XSS plan (FR-14)."""
    from revalid.approval import approve_plan, save_generated_plan
    from revalid.browser import stored_xss_probe
    from revalid.db import session_factory
    from revalid.domain import Finding, RetestPlan, Severity
    from revalid.findings import create_finding
    from revalid.plan import PlanResult

    engine = create_db_engine(IN_MEMORY)
    session = session_factory(engine)()
    create_finding(session, Finding(title="DOM XSS", severity=Severity.HIGH))
    session.commit()
    probe = stored_xss_probe("http://localhost:3000")
    plan = RetestPlan(finding_title="DOM XSS", actions=(probe,), raw={"finding_title": "DOM XSS"})
    save_generated_plan(session, 1, PlanResult(plan=plan))
    approve_plan(session, 1)
    session.close()
    return create_app(engine=engine)


def _executed_xss_evidence(probe: Probe) -> object:
    from revalid.domain import Evidence

    return Evidence(
        request_method="GET",
        request_url=probe.url,
        response_status=200,
        response_body_excerpt=(
            '{"xss_executed": true, "payload_reflected": true, '
            '"dialog_message": "revalid-xss-probe", "final_url": "x"}'
        ),
    )


def test_retest_routes_browser_probe_via_injected_runner() -> None:
    """FR-14: a browser-kind probe runs via the injected runner and yields a verdict."""
    from revalid.app import get_browser_runner

    app = _app_with_browser_plan()
    app.dependency_overrides[get_browser_runner] = lambda: _executed_xss_evidence
    with TestClient(app) as client:
        verdicts = client.post("/api/findings/1/retest").json()
    assert verdicts[0]["probe_kind"] == "browser-xss"
    assert verdicts[0]["status"] == "still_open"  # observed execution -> still open


def test_retest_browser_probe_returns_501_when_unavailable() -> None:
    """FR-14: an approved browser probe without the Playwright extra surfaces as 501."""
    from revalid.app import get_browser_runner
    from revalid.browser import BrowserProbeUnavailableError

    def _raising() -> Callable[[Probe], object]:
        def run(_probe: Probe) -> object:
            raise BrowserProbeUnavailableError()

        return run

    app = _app_with_browser_plan()
    app.dependency_overrides[get_browser_runner] = _raising
    with TestClient(app) as client:
        response = client.post("/api/findings/1/retest")
    assert response.status_code == 501
    assert "browser" in response.json()["detail"].lower()
