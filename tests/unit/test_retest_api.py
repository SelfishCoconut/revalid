"""Unit tests for the rewired retest endpoint: execution requires approval (FR-05).

The probe client is an ``httpx.MockTransport`` and the plan agent is a
``FunctionModel``, so the flow runs off-network. A *generated* action becomes a
``planned-http`` probe, which assesses as ``inconclusive``/``no_assessor`` here;
the login probe's still-open verdict is covered in ``test_retest.py`` and
``test_approval_execute.py``.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
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
        # Generated planned-http probe -> inconclusive (FR-08/09 add matchers);
        # the chokepoint still ran it against /rest/user/login and stamped v1.
        assert verdicts[0]["status"] == "inconclusive"
        assert verdicts[0]["reason_code"] == "no_assessor"
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
