"""Demo for FR-05: nothing executes without approval.

Usage::

    uv run python scripts/demo/approval_gate.py

Runs fully offline against an in-memory app and a mock probe target: import a
finding, show the retest refused (409) before approval, generate a plan, edit it
(v2), approve, and retest — printing the version-stamped verdict.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_plan_agent, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine
from revalid.plan import PlannedAction, build_plan_agent

_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with an authentication token means still open.",
}


def _agent() -> Agent[None, list[PlannedAction]]:
    def respond(_m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": [_ACTION]})]
        )

    return build_plan_agent(FunctionModel(respond))


def _probe_client() -> Iterator[httpx.Client]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def main() -> int:
    """Run the FR-05 approval-gate walkthrough against an offline app."""
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_plan_agent] = _agent
    app.dependency_overrides[get_probe_client] = _probe_client
    with TestClient(app) as client:
        client.post(
            "/findings/import", json={"findings": [{"title": "SQLi login", "severity": "Critical"}]}
        )
        print(
            "1. retest before approval:", client.post("/findings/1/retest").status_code, "(refused)"
        )
        print("2. generate plan:", client.post("/findings/1/plan").json()["status"], "v1")
        edited = client.put("/findings/1/plan", json=[_ACTION]).json()
        print(f"3. edit plan: v{edited['version']} ({edited['origin']})")
        print("4. approve:", client.post("/findings/1/plan/approve").json()["status"])
        verdict = client.post("/findings/1/retest").json()[0]
        print(f"5. retest: {verdict['status']} (executed plan v{verdict['plan_version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
