"""Unit tests for the FR-17 retest agent's deferred-approval gate (ADR-0025).

Spec §12 open-Q1: the suspend-on-approval gate is validated first thing in
Slice 0. This module proves the full deferred-approve-resume cycle at the
agent level — no REST/WS layer involved — using a stateful ``FunctionModel``
(no network, no real LLM): the model proposes ``run_command`` once, the run
pauses without touching the sandbox, and only resuming with ``ToolApproved``
lets the sandbox execute and the model conclude.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests._retest_helpers import (
    has_command_result,
    script_respond_then_conclude,
    script_run_then_conclude,
)

from revalid.domain import VerdictStatus
from revalid.retest_agent import (
    MAX_COMMAND_TIMEOUT,
    ConcludeOutput,
    RetestSessionDeps,
    build_retest_agent,
    clamp_timeout,
)
from revalid.sandbox import CommandResult, FakeSandbox

_RetestAgent = Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]
_RetestRun = AgentRunResult[ConcludeOutput | DeferredToolRequests]
_Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def _script_run_with_timeout(seconds: int) -> _Script:
    """A FunctionModel script that proposes one command with ``timeout_seconds``, then concludes."""

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not has_command_result(messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="run_command",
                        args={
                            "command": "nmap -Pn -T4 --top-ports 100 revalid-juice-shop",
                            "rationale": "scan the target for open services",
                            "timeout_seconds": seconds,
                        },
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={"status": "still_open", "rationale": "port still open"},
                )
            ]
        )

    return script


def _approve_and_resume(
    agent: _RetestAgent, deps: RetestSessionDeps, first: _RetestRun
) -> _RetestRun:
    """Approve the single deferred call in ``first`` and return the resumed run."""
    output = first.output
    assert isinstance(output, DeferredToolRequests)
    [call] = output.approvals
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = ToolApproved()
    return agent.run_sync(
        deps=deps, message_history=first.all_messages(), deferred_tool_results=results
    )


def test_clamp_timeout_bounds_the_agent_choice() -> None:
    """The agent-chosen timeout is clamped to [1, MAX] so it can never hang or be zero."""
    assert clamp_timeout(0) == 1
    assert clamp_timeout(-5) == 1
    assert clamp_timeout(45) == 45
    assert clamp_timeout(99999) == MAX_COMMAND_TIMEOUT


def test_run_command_passes_agent_chosen_timeout_to_sandbox() -> None:
    """The model's ``timeout_seconds`` reaches ``sandbox.exec`` (issue #150)."""
    box = FakeSandbox([CommandResult(stdout="80/tcp open", stderr="", exit_code=0, elapsed_ms=900)])
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda *_: None)
    agent = build_retest_agent(FunctionModel(_script_run_with_timeout(120)))
    first = agent.run_sync("Retest the open-port finding.", deps=deps)
    _approve_and_resume(agent, deps, first)
    assert box.timeouts == [120]


def test_run_command_clamps_an_excessive_timeout() -> None:
    """A model asking for an unbounded wait is clamped to the ceiling before it runs."""
    box = FakeSandbox([CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)])
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda *_: None)
    agent = build_retest_agent(FunctionModel(_script_run_with_timeout(10_000)))
    first = agent.run_sync("Retest.", deps=deps)
    _approve_and_resume(agent, deps, first)
    assert box.timeouts == [MAX_COMMAND_TIMEOUT]


def test_run_command_flags_a_timed_out_command_to_the_model() -> None:
    """A command killed for overrunning (exit 124) is annotated in the tool return."""
    box = FakeSandbox([CommandResult(stdout="", stderr="", exit_code=124, elapsed_ms=30_000)])
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda *_: None)
    agent = build_retest_agent(FunctionModel(_script_run_with_timeout(30)))
    first = agent.run_sync("Retest.", deps=deps)
    second = _approve_and_resume(agent, deps, first)
    returns = [
        str(part.content)
        for msg in second.all_messages()
        for part in getattr(msg, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_command"
    ]
    assert returns and "terminated" in returns[0] and "30s" in returns[0]


def test_agent_exposes_no_set_plan_tool() -> None:
    """6b-ii: the agent no longer proposes plans — set_plan is gone; run_command stays."""
    agent = build_retest_agent(FunctionModel(script_run_then_conclude))
    tools = agent._function_toolset.tools
    assert "set_plan" not in tools
    assert "run_command" in tools


def test_deferred_gate_cycle_runs_command_then_concludes() -> None:
    """Approving the deferred call runs it in the sandbox; the model then concludes."""
    box = FakeSandbox([CommandResult(stdout="{token: ...}", stderr="", exit_code=0, elapsed_ms=12)])
    outputs: list[tuple[str, CommandResult]] = []
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda cmd, res: outputs.append((cmd, res)))
    agent = build_retest_agent(FunctionModel(script_run_then_conclude))

    # Turn 1: proposes a command -> run pauses with a deferred approval request.
    first = agent.run_sync("Retest the SQLi finding.", deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    [call] = first.output.approvals
    assert call.tool_name == "run_command"
    assert "curl" in call.args_as_dict()["command"]
    assert box.commands == []  # NOT executed before approval

    # Approve -> resume: the tool executes in the sandbox, model concludes.
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = ToolApproved()
    second = agent.run_sync(
        deps=deps, message_history=first.all_messages(), deferred_tool_results=results
    )
    assert isinstance(second.output, ConcludeOutput)
    assert second.output.status == VerdictStatus.STILL_OPEN
    assert box.commands == ["curl -s http://revalid-juice-shop:3000/rest/user/login"]
    assert outputs and outputs[0][1].stdout.startswith("{token")


def test_reject_returns_reason_to_the_model() -> None:
    """Denying the deferred call never touches the sandbox."""
    box = FakeSandbox([])  # nothing should execute on a rejection
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda *_: None)
    agent = build_retest_agent(FunctionModel(script_run_then_conclude))
    first = agent.run_sync("Retest.", deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    [call] = first.output.approvals
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = ToolDenied("out of scope host")
    # After denial the (scripted) model concludes; the point is no sandbox exec happened.
    agent.run_sync(deps=deps, message_history=first.all_messages(), deferred_tool_results=results)
    assert box.commands == []


def test_respond_tool_emits_agent_message_and_run_continues() -> None:
    """The non-gated respond tool emits prose mid-run; the run then reaches a verdict."""
    box = FakeSandbox([])  # respond never touches the sandbox
    prose: list[str] = []
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda *_: None, emit_message=prose.append)
    agent = build_retest_agent(FunctionModel(script_respond_then_conclude))

    result = agent.run_sync("Retest the SQLi finding.", deps=deps)

    assert prose == ["the 500 was the WAF rejecting the payload"]
    assert isinstance(result.output, ConcludeOutput)
    assert box.commands == []
