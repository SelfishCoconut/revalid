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
- FIRST, propose a short guiding plan with `set_plan`: an ordered list of a \
few concise steps. A human approves or rejects it before it takes effect. \
Revise it with `set_plan` whenever your strategy changes — every plan change \
is approved the same way.
- Then work one command at a time. Propose a single shell command plus a \
one-line rationale; a human approves or rejects each before it runs.
- The sandbox can reach ONLY the lab target — never the internet or the host.
- Prefer non-destructive verification. Do not attempt to damage the target.
- When you are confident, conclude with a verdict: `still_open` (the issue \
reproduces), `fixed` (it does not), or `inconclusive` (you cannot tell).
"""


class ConcludeOutput(BaseModel):
    """The agent's terminal verdict for a retest session."""

    model_config = ConfigDict(frozen=True)

    status: VerdictStatus
    rationale: str = Field(min_length=1)


def _no_observations() -> list[str]:
    """Default ``drain_observations``: no operator activity to surface."""
    return []


def _no_emit_plan(steps: list[str]) -> None:
    """Default ``emit_plan``: drop the approved plan (agent-unit tests need no sink)."""


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
    #: Records an approved guiding plan (FR-17 Slice 3). Invoked by the gated
    #: ``set_plan`` tool once the human approves it; the orchestrator wires this
    #: to append a ``plan_updated`` transcript event.
    emit_plan: Callable[[list[str]], None] = _no_emit_plan


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
    """Build the FR-17 retest agent: gated ``run_command`` + ``set_plan`` tools + a verdict.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the
            configured backend is used (``REVALID_LLM_MODEL``, Claude by
            default — FR-13); tests pass ``TestModel``/``FunctionModel``.

    Returns:
        An agent whose output is either a :class:`ConcludeOutput` verdict or,
        while a gated ``run_command``/``set_plan`` call awaits human approval, a
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

    @agent.tool(requires_approval=True)
    def set_plan(ctx: RunContext[RetestSessionDeps], steps: list[str], rationale: str) -> str:
        """Propose or revise the guiding plan; takes effect only once the human approves.

        Args:
            ctx: The run context carrying the plan-emit callback.
            steps: The ordered guiding-plan steps (a few concise items) — this
                replaces the whole plan.
            rationale: A one-line reason for this plan (or this revision).

        Returns:
            A short confirmation the approved plan is now in effect.
        """
        ctx.deps.emit_plan(steps)
        return f"Plan set ({len(steps)} steps)."

    return agent
