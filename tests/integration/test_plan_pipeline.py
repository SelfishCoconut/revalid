"""Integration test for FR-04: finding → gated retest plan.

Wires the real components — the FR-06 allowlist guard and the plan generator —
with a deterministic ``FunctionModel`` standing in for the LLM, so the test
proves the end-to-end shape (a finding yields typed, allowlisted probe actions
with indicators) without any network call.
"""

from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.allowlist import load_allowlist
from revalid.domain import Finding, Probe, Severity
from revalid.plan import build_plan_agent, generate_plan

pytestmark = pytest.mark.integration

_BASE_URL = "http://localhost:3000"


def _prompt_text(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        for part in getattr(message, "parts", ()):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def _planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Propose one GET action per affected endpoint mentioned in the prompt."""
    text = _prompt_text(messages)
    actions: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("Affected endpoints:"):
            for ep in line.removeprefix("Affected endpoints:").split(","):
                ep = ep.strip()
                if ep and ep != "(none stated)":
                    actions.append(
                        {
                            "method": "GET",
                            "target": ep,
                            "headers": {},
                            "json_body": None,
                            "expected_indicator": f"A 200 from {ep} means still present.",
                        }
                    )
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"response": actions})]
    )


def test_finding_yields_allowlisted_typed_plan() -> None:
    finding = Finding(
        title="Broken Access Control on Basket",
        severity=Severity.HIGH,
        affected_endpoints=("/rest/basket/1", "/rest/basket/2"),
        reproduction_steps=("Log in as a user", "Request another user's basket id"),
    )
    agent = build_plan_agent(FunctionModel(_planner))

    result = generate_plan(agent, finding, load_allowlist(), _BASE_URL)

    assert not result.rejected and not result.error
    assert result.plan.finding_title == "Broken Access Control on Basket"
    assert all(isinstance(a, Probe) for a in result.plan.actions)
    assert [a.url for a in result.plan.actions] == [
        "http://localhost:3000/rest/basket/1",
        "http://localhost:3000/rest/basket/2",
    ]
    # AC2: every action states its still-open indicator.
    assert all(a.expected_indicator for a in result.plan.actions)
