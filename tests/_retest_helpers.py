"""Shared stateful ``FunctionModel`` test helpers for the FR-17 retest agent.

Not a test module itself (no ``test_`` prefix) — imported by
``tests/unit/test_retest_agent.py`` and by later tasks (5/6/7) that also need
to script a model through the deferred-approval ``run_command`` gate followed
by a :class:`~revalid.retest_agent.ConcludeOutput` verdict.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo


def has_command_result(messages: list[ModelMessage]) -> bool:
    """Return True once a ``run_command`` :class:`ToolReturnPart` is in history."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == "run_command"
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
    )


def script_run_then_conclude(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stateful scripted model: propose ``run_command`` once, then conclude.

    Before a ``run_command`` result is present in history, always calls
    ``run_command`` with a fixed command/rationale. Once the tool has returned
    (i.e. it was approved and executed), calls the ``ConcludeOutput`` output
    tool with a ``still_open`` verdict.

    Verified empirically: with ``output_type=[ConcludeOutput, DeferredToolRequests]``,
    ``info.output_tools`` holds exactly one entry — ``DeferredToolRequests`` is the
    deferred-tool-call marker, not a registered output tool — so
    ``info.output_tools[0]`` is unambiguously ConcludeOutput's synthesized output
    tool. Its ``.name`` is Pydantic AI's default synthesized name (``"final_result"``),
    *not* the class name ``"ConcludeOutput"``; selecting by name would raise
    ``StopIteration``, which is why this indexes instead.
    """
    if not has_command_result(messages):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_command",
                    args={
                        "command": "curl -s http://revalid-juice-shop:3000/rest/user/login",
                        "rationale": "retry the login-bypass payload",
                    },
                )
            ]
        )
    output_tool = info.output_tools[0].name
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=output_tool,
                args={"status": "still_open", "rationale": "auth still bypassable"},
            )
        ]
    )
