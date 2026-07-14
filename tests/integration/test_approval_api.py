"""Integration test for the FR-05 approval + retest HTTP flow (no network).

The plan agent is overridden with a FunctionModel; the probe client is a
MockTransport. Proves: no execution without approval (AC1), and edits are
versioned with the executed version stamped (AC2). A *generated* action becomes a
``planned-http`` probe, which assesses as ``inconclusive``/``no_assessor`` (generic
matching is FR-08/FR-09) — the still-open verdict for the login probe is proven in
``tests/unit/test_approval_execute.py`` and the live-lab system test.
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
from revalid.plan import PlannedAction, build_plan_agent

pytestmark = pytest.mark.integration

_IMPORT: dict[str, Any] = {
    "scan_type": "Manual pentest",
    "findings": [
        {
            "title": "SQL injection auth bypass in login",
            "severity": "Critical",
            "endpoints": ["http://localhost:3000/rest/user/login"],
            "steps_to_reproduce": "1. POST ' OR 1=1--",
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


def _agent_proposing(*actions: dict[str, Any]) -> Agent[None, list[PlannedAction]]:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": list(actions)})]
        )

    return build_plan_agent(FunctionModel(respond))


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))

    def probe_override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = probe_override
    app.dependency_overrides[get_plan_agent] = lambda: _agent_proposing(_SQLI_ACTION)
    return TestClient(app)


def _token(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def test_retest_refused_until_approved_then_executes() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)

        # AC1: no approved plan -> 409, and nothing executes.
        assert client.post("/findings/1/retest").status_code == 409

        assert client.post("/findings/1/plan").json()["status"] == "proposed"
        assert client.post("/findings/1/plan/approve").json()["status"] == "approved"

        verdicts = client.post("/findings/1/retest").json()
        # A generated planned-http probe assesses as inconclusive (FR-08/09 later),
        # but the chokepoint ran it and stamped the executed version (AC2).
        assert [v["status"] for v in verdicts] == ["inconclusive"]
        assert verdicts[0]["reason_code"] == "no_assessor"
        assert verdicts[0]["plan_version"] == 1


def test_edit_creates_v2_and_execution_uses_it() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        client.post("/findings/1/plan")

        edited = client.put("/findings/1/plan", json=[_SQLI_ACTION]).json()
        assert edited["version"] == 2 and edited["origin"] == "edited"

        plans = client.get("/findings/1/plans").json()
        assert {p["version"]: p["status"] for p in plans} == {1: "superseded", 2: "proposed"}

        client.post("/findings/1/plan/approve")
        assert client.post("/findings/1/retest").json()[0]["plan_version"] == 2


def test_edit_all_off_allowlist_is_422() -> None:
    off = {"method": "GET", "target": "http://evil.example/", "expected_indicator": "x"}
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        client.post("/findings/1/plan")
        assert client.put("/findings/1/plan", json=[off]).status_code == 422


def test_approve_without_plan_is_409() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        assert client.post("/findings/1/plan/approve").status_code == 409


def test_reject_blocks_execution() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        client.post("/findings/1/plan")
        assert client.post("/findings/1/plan/reject").json()["status"] == "rejected"
        # a rejected plan is not approved -> retest still refused (AC1)
        assert client.post("/findings/1/retest").status_code == 409
        # nothing left to reject now
        assert client.post("/findings/1/plan/reject").status_code == 409
