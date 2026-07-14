"""Integration test: the FR-11 acceptance path is operable end-to-end over /api.

Drives ingest → extract (FR-01/FR-03) → generate plan (FR-04) → approve (FR-05)
→ retest (FR-07/FR-09) entirely through the ``/api`` surface, wiring the real
components with deterministic LLM stand-ins and a MockTransport probe client —
the automated form of "operable from the UI alone", with no network and no lab.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_extraction_agent, get_plan_agent, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine
from revalid.extract import ExtractedFinding
from revalid.plan import PlannedAction, build_plan_agent

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"

_SQLI_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with a token means still open.",
}


def _plan_agent(*actions: dict[str, Any]) -> Agent[None, list[PlannedAction]]:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": list(actions)})]
        )

    return build_plan_agent(FunctionModel(respond))


def _token(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def test_full_flow_operable_over_api(
    extraction_agent: Agent[None, list[ExtractedFinding]],
) -> None:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_extraction_agent] = lambda: extraction_agent
    app.dependency_overrides[get_plan_agent] = lambda: _plan_agent(_SQLI_ACTION)

    def probe_override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(_token)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = probe_override
    client = TestClient(app)

    # 1. ingest: the upload's background extraction runs before the response returns.
    upload = client.post(
        "/api/reports",
        files={"file": ("report.pdf", FIXTURE.read_bytes(), "application/pdf")},
    )
    assert upload.status_code == 202
    report_id = upload.json()["id"]
    assert client.get(f"/api/reports/{report_id}").json()["status"] == "ready"

    # 2. a finding from that report is now listed.
    finding_id = client.get("/api/findings", params={"report_id": report_id}).json()[0]["id"]

    # 3. plan → approve → retest, every step over /api.
    assert client.post(f"/api/findings/{finding_id}/plan").json()["status"] == "proposed"
    assert client.post(f"/api/findings/{finding_id}/plan/approve").json()["status"] == "approved"
    verdicts = client.post(f"/api/findings/{finding_id}/retest").json()

    assert verdicts and verdicts[0]["plan_version"] == 1
    assert verdicts[0]["evidence"]["response_status"] == 200
