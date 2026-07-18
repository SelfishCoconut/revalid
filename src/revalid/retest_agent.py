"""FR-17 / M6 agentic retest agent (ADR-0025, Slice 0).

One gated ``run_command`` tool (Pydantic AI deferred approval) + a
``ConcludeOutput`` structured verdict. The orchestrator (retest_session.py)
runs the agent step-by-step, pausing on each proposed command for human
approval and resuming with ``ToolApproved``/``ToolDenied``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import VerdictStatus
from revalid.llm import resolve_model
from revalid.sandbox import CommandResult, Sandbox

_MAX_TOOL_RETRIES = 2

_INSTRUCTIONS = """\
You are a penetration-test *retester*. You are given one finding to re-verify \
against an authorised lab target that is reachable from your sandbox.

Rules:
- Work one command at a time. Propose a single shell command plus a \
one-line rationale; a human approves or rejects each before it runs.
- The sandbox can reach ONLY the lab target — never the internet or the host.
- Prefer non-destructive verification. Do not attempt to damage the target.
- When you are confident, conclude with a verdict: `still_open` (the issue \
reproduces), `fixed` (it does not), or `inconclusive` (you cannot tell).
- The operator may message you at any time; their message arrives as a new \
turn. Always address it: answer questions with `respond`, then continue; fold \
any steering into your plan and commands. Use `respond` sparingly — to answer \
or for a brief status note, not to narrate every step.
"""


class ConcludeOutput(BaseModel):
    """The agent's terminal verdict for a retest session."""

    model_config = ConfigDict(frozen=True)

    status: VerdictStatus
    rationale: str = Field(min_length=1)


def _no_observations() -> list[str]:
    """Default ``drain_observations``: no operator activity to surface."""
    return []


def _no_emit_message(message: str) -> None:
    """Default ``emit_message``: drop agent prose (agent-unit tests need no sink)."""


@dataclass
class RetestSessionDeps:
    """Runtime dependencies injected into the retest agent's tools."""

    sandbox: Sandbox
    emit_output: Callable[[str, CommandResult], None]
    command_timeout: float = 30.0
    #: Returns (and clears) any manual operator commands run since the agent's
    #: last turn, so the agent observes what the human did (FR-17 Slice 2). The
    #: default surfaces nothing — the human-command path (`!`) injects the real
    #: drain via the orchestrator's :func:`~revalid.retest_session._make_deps`.
    drain_observations: Callable[[], list[str]] = _no_observations
    #: Records the agent's prose replies to the operator (FR-17 Slice 4). Invoked
    #: by the non-gated ``respond`` tool; the orchestrator wires this to append an
    #: ``agent_message`` transcript event. The default drops it (agent-unit tests).
    emit_message: Callable[[str], None] = _no_emit_message


def _format_result(result: CommandResult) -> str:
    """Render a command result as the tool-return text the model observes."""
    return (
        f"exit_code={result.exit_code} elapsed_ms={result.elapsed_ms}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def format_observations(observations: list[str]) -> str:
    """Render buffered operator activity as a block the agent reads on its next turn.

    Returns an empty string when there is nothing to surface, so callers can
    append it unconditionally.

    Args:
        observations: Human-run command summaries buffered since the last turn.

    Returns:
        A labelled block to append to the next tool result, or ``""`` if empty.
    """
    if not observations:
        return ""
    return "\n\n--- operator activity while you waited ---\n" + "\n".join(observations)


def build_retest_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]:
    """Build the FR-17 retest agent: a gated ``run_command`` tool + a verdict.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the
            configured backend is used (``REVALID_LLM_MODEL``, Claude by
            default — FR-13); tests pass ``TestModel``/``FunctionModel``.

    Returns:
        An agent whose output is either a :class:`ConcludeOutput` verdict or,
        while a gated ``run_command`` call awaits human approval, a
        :class:`~pydantic_ai.DeferredToolRequests`.
    """
    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests] = Agent(
        model if model is not None else resolve_model(),
        deps_type=RetestSessionDeps,
        output_type=[ConcludeOutput, DeferredToolRequests],
        instructions=_INSTRUCTIONS,
        retries=_MAX_TOOL_RETRIES,
        defer_model_check=True,
    )

    @agent.tool(requires_approval=True)
    def run_command(ctx: RunContext[RetestSessionDeps], command: str, rationale: str) -> str:
        """Run one shell command in the egress-locked sandbox and return its output.

        Args:
            ctx: The run context carrying the sandbox + output-emit callback.
            command: The exact shell command to execute (lab target only).
            rationale: A one-line reason this command advances the retest.

        Returns:
            The command's exit code, timing, stdout and stderr as text.
        """
        result = ctx.deps.sandbox.exec(command, timeout=ctx.deps.command_timeout)
        ctx.deps.emit_output(command, result)
        return _format_result(result) + format_observations(ctx.deps.drain_observations())

    @agent.tool
    def respond(ctx: RunContext[RetestSessionDeps], message: str) -> str:
        """Send a short prose message to the operator (e.g. answer a question).

        Use this to reply to the operator or give a brief status note — not to
        narrate every step. It runs nothing; after it you continue with your
        plan, a command, or a verdict.

        Args:
            ctx: The run context carrying the message-emit callback.
            message: The prose to show the operator in the chat.

        Returns:
            A short confirmation the message was delivered.
        """
        ctx.deps.emit_message(message)
        return "Delivered to the operator."

    return agent
