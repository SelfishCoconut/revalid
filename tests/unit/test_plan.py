"""Unit tests for retest-goal generation (FR-04 repurposed — ADR-0032).

No network and no real model: Pydantic AI's ``FunctionModel`` drives exact
structured output. (The old batch probe-plan generation retired with the batch
path in FR-17 6b-iii.)
"""

from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.domain import Finding, Severity
from revalid.plan import build_goal_agent, generate_goal


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
