"""Shared stateful ``FunctionModel`` test helpers for the FR-17 retest agent.

Not a test module itself (no ``test_`` prefix) — imported by
``tests/unit/test_retest_agent.py`` and by later tasks (5/6/7) that also need
to script a model through the deferred-approval ``run_command`` gate followed
by a :class:`~revalid.retest_agent.ConcludeOutput` verdict.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel


def streaming(script: Callable[[list[ModelMessage], AgentInfo], ModelResponse]) -> FunctionModel:
    """Wrap a scripted model so it answers *streamed* requests too (issue #140).

    The orchestrator runs each turn through ``run_stream_events``, which asks the
    model for a streamed request, and ``FunctionModel`` refuses one unless it is
    given a ``stream_function``. This replays the very same scripted
    ``ModelResponse`` as one delta batch, so every existing script keeps its
    behaviour and its assertions.

    One batch rather than character-by-character on purpose: these tests are
    about what a turn *decides* — the proposal, the gate, the verdict — not about
    token granularity. Live token behaviour is covered by ``test_deltas.py`` and
    was verified against a real model, where one turn produced 746 thinking
    deltas.
    """

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        response = script(messages, info)
        calls: DeltaToolCalls = {}
        for index, part in enumerate(response.parts):
            if isinstance(part, ToolCallPart):
                calls[index] = DeltaToolCall(
                    name=part.tool_name,
                    json_args=part.args_as_json_str(),
                    tool_call_id=part.tool_call_id,
                )
            elif isinstance(part, TextPart):
                yield part.content
        if calls:
            yield calls

    return FunctionModel(script, stream_function=stream_fn)


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


def script_always_propose(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stateful scripted model: always propose ``run_command``, never conclude.

    Unlike :func:`script_run_then_conclude`, this never calls the output tool —
    every turn re-proposes the same ``run_command`` call regardless of history.
    Used to exercise an agent that never concludes on its own: the orchestrator
    keeps gating each command and only ever pauses when the agent hands back,
    never on a step count (ADR-0034).
    """
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="run_command",
                args={
                    "command": "curl -s http://revalid-juice-shop:3000/rest/user/whoami",
                    "rationale": "keep probing",
                },
            )
        ]
    )


def has_tool_result(messages: list[ModelMessage], tool_name: str) -> bool:
    """Return True once a ``ToolReturnPart`` for ``tool_name`` is in history."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == tool_name
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
    )


def script_respond_then_conclude(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stateful scripted model: call ``respond`` once, then conclude (FR-17 Slice 4).

    Proves the non-gated ``respond`` tool emits prose mid-run and the run then
    continues to a verdict without proposing any command. Concludes ``still_open``
    (a real determination) — the agent can no longer self-conclude ``inconclusive``
    (ADR-0034), which now pauses for the operator instead of terminating.
    """
    if not has_tool_result(messages, "respond"):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="respond",
                    args={"message": "the 500 was the WAF rejecting the payload"},
                )
            ]
        )
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={
                    "status": "still_open",
                    "rationale": "answered the operator, still reproduces",
                },
            )
        ]
    )


def script_conclude_inconclusive(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Immediately conclude ``inconclusive`` — the agent handing back to the operator.

    Under ADR-0034 this does not terminate: the orchestrator reinterprets an
    ``inconclusive`` conclusion as an exhausted-options pause for guidance.
    """
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={"status": "inconclusive", "rationale": "exhausted my options, need guidance"},
            )
        ]
    )


def script_inconclusive_then_conclude_on_message(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    """Hand back ``inconclusive`` until the operator provides guidance, then conclude.

    With only the initial user turn present the agent pauses (``inconclusive``);
    once a second user turn (delivered operator guidance) arrives it concludes
    ``still_open`` — exercising the resume path of ``continue_session`` (ADR-0034).
    """
    if operator_message_count(messages) > 1:
        args = {"status": "still_open", "rationale": "with the operator's steer, confirmed open"}
    else:
        args = {"status": "inconclusive", "rationale": "need guidance to proceed"}
    return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


def operator_message_count(messages: list[ModelMessage]) -> int:
    """Count user-turn messages in history.

    A retest starts with exactly one user turn (the finding goal); each operator
    chat message delivered on an approve/reject resume adds one more.
    """
    return sum(
        1
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, UserPromptPart)
    )


def script_run_then_conclude_noting_message(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    """Propose a command, then conclude reporting whether an operator chat message arrived.

    The verdict rationale is ``"saw-message"`` iff more than one user turn is
    present (the initial goal plus a delivered chat message), else ``"no-message"``.
    """
    if not has_command_result(messages):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_command",
                    args={
                        "command": "curl -s http://revalid-juice-shop:3000/",
                        "rationale": "probe",
                    },
                )
            ]
        )
    rationale = "saw-message" if operator_message_count(messages) > 1 else "no-message"
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={"status": "still_open", "rationale": rationale},
            )
        ]
    )
