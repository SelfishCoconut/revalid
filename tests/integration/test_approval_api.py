"""Integration test for the FR-05 approval + retest HTTP flow (no network).

The plan agent is overridden with a FunctionModel; the probe client is a
MockTransport. Proves: no execution without approval (AC1), and edits are
versioned with the executed version stamped (AC2). A *generated* action for the
SQLi-login finding is classified as ``sqli-login-bypass`` (ADR-0019) and assesses
as a conclusive ``still_open`` given the mock's token response.
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


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    plan_actions: tuple[dict[str, Any], ...] = (_SQLI_ACTION,),
) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))

    def probe_override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = probe_override
    app.dependency_overrides[get_plan_agent] = lambda: _agent_proposing(*plan_actions)
    return TestClient(app)


def _token(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def test_retest_refused_until_approved_then_executes() -> None:
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)

        # AC1: no approved plan -> 409, and nothing executes.
        assert client.post("/api/findings/1/retest").status_code == 409

        # Generation is async (ADR-0022): the POST returns 202 with an in-flight
        # version; the background task settles it to proposed before the reply lands.
        started = client.post("/api/findings/1/plan")
        assert started.status_code == 202
        assert started.json()["status"] == "generating"
        assert client.get("/api/findings/1/plans").json()[-1]["status"] == "proposed"
        assert client.post("/api/findings/1/plan/approve").json()["status"] == "approved"

        verdicts = client.post("/api/findings/1/retest").json()
        # The generated action is classified as sqli-login-bypass (ADR-0019); the
        # mock returns a token, so it assesses as a conclusive still_open — and the
        # chokepoint stamped the executed version (AC2).
        assert [v["status"] for v in verdicts] == ["still_open"]
        assert verdicts[0]["reason_code"] == "sqli_auth_bypass_succeeded"
        assert verdicts[0]["plan_version"] == 1


def test_edit_creates_v2_and_execution_uses_it() -> None:
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        client.post("/api/findings/1/plan")

        edited = client.put("/api/findings/1/plan", json=[_SQLI_ACTION]).json()
        assert edited["version"] == 2 and edited["origin"] == "edited"

        plans = client.get("/api/findings/1/plans").json()
        assert {p["version"]: p["status"] for p in plans} == {1: "superseded", 2: "proposed"}

        client.post("/api/findings/1/plan/approve")
        assert client.post("/api/findings/1/retest").json()[0]["plan_version"] == 2


def test_edit_all_off_allowlist_is_422() -> None:
    off = {"method": "GET", "target": "http://evil.example/", "expected_indicator": "x"}
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        client.post("/api/findings/1/plan")
        assert client.put("/api/findings/1/plan", json=[off]).status_code == 422


def test_approve_without_plan_is_409() -> None:
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        assert client.post("/api/findings/1/plan/approve").status_code == 409


def test_reject_blocks_execution() -> None:
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        client.post("/api/findings/1/plan")
        assert client.post("/api/findings/1/plan/reject").json()["status"] == "rejected"
        # a rejected plan is not approved -> retest still refused (AC1)
        assert client.post("/api/findings/1/retest").status_code == 409
        # nothing left to reject now
        assert client.post("/api/findings/1/plan/reject").status_code == 409


def test_generate_empty_plan_settles_failed_and_blocks_approval() -> None:
    with _client(_token, plan_actions=()) as client:
        client.post("/api/findings/import", json=_IMPORT)
        # Async generation (ADR-0022): the 202 reserves a version; an empty plan
        # settles it to failed (with the reason) rather than raising 422 up front.
        assert client.post("/api/findings/1/plan").status_code == 202
        [plan] = client.get("/api/findings/1/plans").json()
        assert plan["status"] == "failed"
        assert plan["error"] == "no runnable actions could be planned for this finding"
        # A failed version is not approvable and never runs (AC1).
        assert client.post("/api/findings/1/plan/approve").status_code == 409
        assert client.post("/api/findings/1/retest").status_code == 409


def test_generation_records_operator_instructions() -> None:
    # FR-04 (ADR-0023): the {instructions} body is recorded in the plan lineage.
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/plan", json={"instructions": "also check /admin"})
        assert started.status_code == 202
        assert started.json()["raw"]["instructions"] == "also check /admin"
        assert client.get("/api/findings/1/plans").json()[-1]["raw"]["instructions"] == (
            "also check /admin"
        )


def test_regenerate_supersedes_approved_and_blocks_retest_until_reapproved() -> None:
    # ADR-0023: re-POSTing /plan while approved supersedes the approved version;
    # nothing runs until the fresh plan is approved again (FR-05 gate preserved).
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        client.post("/api/findings/1/plan")
        assert client.post("/api/findings/1/plan/approve").json()["status"] == "approved"

        assert client.post("/api/findings/1/plan").status_code == 202  # regenerate
        assert client.post("/api/findings/1/retest").status_code == 409  # approved gone
        statuses = {p["version"]: p["status"] for p in client.get("/api/findings/1/plans").json()}
        assert statuses == {1: "superseded", 2: "proposed"}

        client.post("/api/findings/1/plan/approve")
        assert client.post("/api/findings/1/retest").json()[0]["plan_version"] == 2


def test_revise_unapproves_into_editable_proposal() -> None:
    # ADR-0023: revise supersedes the approved plan into an editable proposed copy.
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        client.post("/api/findings/1/plan")
        client.post("/api/findings/1/plan/approve")

        revised = client.post("/api/findings/1/plan/revise")
        assert revised.status_code == 200
        assert revised.json()["status"] == "proposed"
        assert revised.json()["origin"] == "revised"
        # un-approved: retest refused until re-approval (AC1).
        assert client.post("/api/findings/1/retest").status_code == 409
        statuses = {p["version"]: p["status"] for p in client.get("/api/findings/1/plans").json()}
        assert statuses == {1: "superseded", 2: "proposed"}


def test_revise_without_approved_plan_is_409() -> None:
    with _client(_token) as client:
        client.post("/api/findings/import", json=_IMPORT)
        client.post("/api/findings/1/plan")  # proposed, not approved
        assert client.post("/api/findings/1/plan/revise").status_code == 409
