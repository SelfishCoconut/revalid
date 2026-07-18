"""Unit tests for retest-plan generation (FR-04).

No network and no real model: Pydantic AI's ``FunctionModel`` drives exact
proposed actions so we can prove the deterministic gate — typed-only, allowlist
enforcement, non-destructive methods, and the schema-validation gate.
"""

from typing import Any

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.allowlist import TargetGuard
from revalid.domain import Finding, Severity
from revalid.llm import DEFAULT_MODEL
from revalid.plan import build_goal_agent, build_plan_agent, generate_goal, generate_plan


def _goal_model(steps: list[str]) -> FunctionModel:
    def gen(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"steps": steps})]
        )

    return FunctionModel(gen)


def test_generate_goal_returns_generic_steps() -> None:
    agent = build_goal_agent(
        _goal_model(["Re-exercise the reported condition", "Observe whether it still occurs"])
    )
    steps = generate_goal(agent, Finding(title="Broken access control", severity=Severity.HIGH))
    assert steps == ("Re-exercise the reported condition", "Observe whether it still occurs")


def test_generate_goal_degrades_to_empty_on_model_failure() -> None:
    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise UnexpectedModelBehavior("model unavailable")

    agent = build_goal_agent(FunctionModel(boom))
    assert generate_goal(agent, Finding(title="X", severity=Severity.LOW)) == ()


_BASE_URL = "http://localhost:3000"
_GUARD = TargetGuard(frozenset({"http://localhost:3000/*"}))

_LOGIN_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with an authentication token means still open.",
}


def _finding() -> Finding:
    return Finding(
        title="SQL Injection in Login",
        severity=Severity.CRITICAL,
        attack_vector="Tautology in the email field.",
        affected_endpoints=("/rest/user/login",),
        reproduction_steps=("Open /#/login", "Submit ' OR 1=1--"),
    )


def _model_proposing(*actions: dict[str, Any]) -> FunctionModel:
    """A model that always proposes ``actions`` as its structured output."""

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": list(actions)})]
        )

    return FunctionModel(respond)


def _plan_for(*actions: dict[str, Any]) -> Any:
    agent = build_plan_agent(_model_proposing(*actions))
    return generate_plan(agent, _finding(), _GUARD, _BASE_URL)


def test_operator_instructions_steer_the_prompt_and_are_recorded() -> None:
    """FR-04 (ADR-0023): guidance reaches the model and lands in the plan lineage."""
    captured: dict[str, str] = {}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["prompt"] = str(messages)
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": [_LOGIN_ACTION]})]
        )

    agent = build_plan_agent(FunctionModel(respond))
    result = generate_plan(agent, _finding(), _GUARD, _BASE_URL, "also probe /admin for IDOR")

    assert "also probe /admin for IDOR" in captured["prompt"]
    assert "operator instructions" in captured["prompt"].lower()
    assert result.plan.raw["instructions"] == "also probe /admin for IDOR"


def test_no_instructions_records_empty_guidance() -> None:
    result = _plan_for(_LOGIN_ACTION)
    assert result.plan.raw["instructions"] == ""


def test_valid_action_becomes_typed_allowlisted_probe() -> None:
    result = _plan_for(_LOGIN_ACTION)

    assert not result.rejected and not result.error
    [probe] = result.plan.actions
    assert probe.method == "POST"
    assert probe.url == "http://localhost:3000/rest/user/login"  # relative target resolved
    assert probe.expected_indicator  # AC2: every action states an indicator
    assert result.plan.finding_title == "SQL Injection in Login"
    assert result.plan.version == 1


def test_relative_and_absolute_allowlisted_targets_resolve() -> None:
    absolute = {
        **_LOGIN_ACTION,
        "target": "http://localhost:3000/rest/products/search?q=x",
        "method": "GET",
    }
    result = _plan_for(_LOGIN_ACTION, absolute)
    urls = [p.url for p in result.plan.actions]
    assert urls == [
        "http://localhost:3000/rest/user/login",
        "http://localhost:3000/rest/products/search?q=x",
    ]


def test_off_allowlist_target_is_dropped_never_run() -> None:
    # FR-04 AC1 / FR-06: a model-proposed target outside the allowlist is dropped.
    evil = {**_LOGIN_ACTION, "target": "http://169.254.169.254/latest/meta-data/"}
    result = _plan_for(evil)

    assert result.plan.actions == ()
    [rejected] = result.rejected
    assert rejected.reason == "not_allowlisted"


def test_hostless_target_is_dropped_as_invalid() -> None:
    # A target that resolves to a schemeless/hostless URL cannot be allowlist-
    # checked, so the gate drops it rather than letting it through.
    result = _plan_for({**_LOGIN_ACTION, "method": "GET", "target": "mailto:admin@juice-sh.op"})
    assert result.plan.actions == ()
    assert result.rejected[0].reason == "invalid_target"


def test_destructive_method_is_dropped() -> None:
    for verb in ("DELETE", "PUT", "PATCH"):
        result = _plan_for({**_LOGIN_ACTION, "method": verb})
        assert result.plan.actions == ()
        assert result.rejected[0].reason == "unsafe_method"


def test_mixed_plan_keeps_only_gated_actions() -> None:
    result = _plan_for(
        _LOGIN_ACTION,
        {**_LOGIN_ACTION, "method": "DELETE"},
        {**_LOGIN_ACTION, "target": "http://evil.example/"},
    )
    assert len(result.plan.actions) == 1
    assert {r.reason for r in result.rejected} == {"unsafe_method", "not_allowlisted"}
    # raw lineage records the counts for the audit trail (NFR-02).
    assert result.plan.raw["proposed"] == 3
    assert result.plan.raw["rejected"] == 2


def test_invalid_output_is_flagged_not_guessed() -> None:
    # An empty indicator fails PlannedAction validation; after retries the plan
    # must come back empty with an error — never a malformed action (the gate).
    bad = {**_LOGIN_ACTION, "expected_indicator": ""}
    result = _plan_for(bad)

    assert result.plan.actions == ()
    assert result.error
    assert result.plan.raw["model"].startswith("function")


def test_lineage_records_model_and_base_url() -> None:
    result = _plan_for(_LOGIN_ACTION)
    assert result.plan.raw["source"] == "plan_generation"
    assert result.plan.raw["model"].startswith("function")
    assert result.plan.raw["base_url"] == _BASE_URL


def test_default_agent_uses_configured_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVALID_LLM_MODEL", raising=False)
    agent = build_plan_agent()
    assert agent.model == DEFAULT_MODEL


def test_gate_actions_splits_survivors_from_rejects() -> None:
    from revalid.plan import PlannedAction, gate_actions

    ok = PlannedAction(**_LOGIN_ACTION)
    bad = PlannedAction(**{**_LOGIN_ACTION, "target": "http://evil.example/"})
    probes, rejected = gate_actions([ok, bad], _GUARD, _BASE_URL)

    assert [p.url for p in probes] == ["http://localhost:3000/rest/user/login"]
    assert [r.reason for r in rejected] == ["not_allowlisted"]


def test_action_kind_hint_tags_the_probe() -> None:
    # ADR-0019: the model's lenient kind hint is normalized onto the probe kind,
    # selecting the assessor/renderer — here an IDOR read over another basket.
    action = {**_LOGIN_ACTION, "method": "GET", "target": "/rest/basket/2", "kind": "idor"}
    [probe] = _plan_for(action).plan.actions
    assert probe.kind == "access-control"


def test_finding_fallback_tags_the_probe_when_no_hint() -> None:
    # No kind hint: the fallback classifies from the finding — a SQLi-login here.
    [probe] = _plan_for(_LOGIN_ACTION).plan.actions
    assert probe.kind == "sqli-login-bypass"
