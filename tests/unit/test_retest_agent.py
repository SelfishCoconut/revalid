"""Unit tests for the FR-17 retest agent's deferred-approval gate (ADR-0025).

Spec §12 open-Q1: the suspend-on-approval gate is validated first thing in
Slice 0. This module proves the full deferred-approve-resume cycle at the
agent level — no REST/WS layer involved — using a stateful ``FunctionModel``
(no network, no real LLM): the model proposes ``run_command`` once, the run
pauses without touching the sandbox, and only resuming with ``ToolApproved``
lets the sandbox execute and the model conclude.
"""

from __future__ import annotations

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.models.function import FunctionModel
from tests._retest_helpers import script_respond_then_conclude, script_run_then_conclude

from revalid.domain import VerdictStatus
from revalid.retest_agent import ConcludeOutput, RetestSessionDeps, build_retest_agent
from revalid.sandbox import CommandResult, FakeSandbox


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
