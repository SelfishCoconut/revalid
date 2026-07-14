"""Demo for FR-04: derive a gated retest plan from a finding.

Usage::

    uv run python scripts/demo/plan_retest.py

Backend selection is configuration-only (FR-13): with ``REVALID_LLM_MODEL`` set
(e.g. ``ollama:<model>`` + ``OLLAMA_BASE_URL``) that backend proposes the plan;
otherwise, with ``ANTHROPIC_API_KEY`` set, Claude does. With neither, a
deterministic offline stand-in runs so the demo always works. Either way the
FR-06 allowlist gate is enforced in code: a deliberately off-allowlist action is
included below to show it being dropped, never planned.
"""

from __future__ import annotations

import os
import sys

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.allowlist import load_allowlist
from revalid.domain import Finding, Severity
from revalid.llm import DEFAULT_MODEL, MODEL_ENV, resolve_model
from revalid.plan import build_plan_agent, generate_plan
from revalid.retest import lab_base_url

_FINDING = Finding(
    title="SQL Injection in Login Form",
    severity=Severity.CRITICAL,
    description="The login endpoint concatenates the email field into a SQL query.",
    attack_vector="A tautology payload in the email field bypasses authentication.",
    affected_endpoints=("/rest/user/login",),
    reproduction_steps=(
        "Open the login page at /#/login",
        "Submit email ' OR 1=1-- with any password",
        "Observe an authenticated session is returned",
    ),
)


def _offline_planner(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Deterministic stand-in: one valid action + one off-allowlist action."""
    actions = [
        {
            "method": "POST",
            "target": "/rest/user/login",
            "headers": {"Content-Type": "application/json"},
            "json_body": {"email": "' OR 1=1--", "password": "x"},
            "expected_indicator": "HTTP 200 carrying an authentication token means still open.",
        },
        {
            "method": "GET",
            "target": "http://169.254.169.254/latest/meta-data/",
            "headers": {},
            "json_body": None,
            "expected_indicator": "(a report-sourced target the gate must reject)",
        },
    ]
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"response": actions})]
    )


def _select_model() -> tuple[Model | KnownModelName | str, str]:
    """Pick the configured backend, else Claude with a key, else the stand-in."""
    if os.environ.get(MODEL_ENV):
        model = resolve_model()
        return model, f"{model} (live, from {MODEL_ENV})"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return DEFAULT_MODEL, f"{DEFAULT_MODEL} (live)"
    return FunctionModel(_offline_planner), "offline stand-in (no ANTHROPIC_API_KEY)"


def main() -> int:
    """Generate and print a gated retest plan for the sample finding."""
    model, label = _select_model()
    base_url = lab_base_url()
    print(f"Finding: {_FINDING.title}\nModel: {label}\nBase URL (allowlisted): {base_url}\n")

    result = generate_plan(build_plan_agent(model), _FINDING, load_allowlist(), base_url)
    if result.error:
        print(f"Plan generation failed the schema gate: {result.error}", file=sys.stderr)
        return 1

    print(f"Plan v{result.plan.version} — {len(result.plan.actions)} action(s):")
    for i, probe in enumerate(result.plan.actions, 1):
        print(f"  [{i}] {probe.method} {probe.url}")
        print(f"      indicator: {probe.expected_indicator}")
    for rejected in result.rejected:
        print(f"  dropped ({rejected.reason}): {rejected.action.method} {rejected.action.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
