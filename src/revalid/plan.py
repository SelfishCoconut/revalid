"""Retest-goal generation from findings (FR-04, repurposed — ADR-0032).

FR-04's plan generator was repurposed (FR-17 6b-ii) from producing HTTP probes
into producing a **retest goal**: a few concise, tool-agnostic verification steps
the agentic console's agent works to. A Pydantic AI agent proposes a
:class:`GeneratedGoal` from the finding (ADR-0009 schema-gate pattern; model
chosen by ``REVALID_LLM_MODEL`` — FR-13). The old batch probe-plan path was
removed with the batch execution in FR-17 6b-iii.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import Finding
from revalid.llm import resolve_model

_MAX_OUTPUT_RETRIES = 3


class GeneratedGoal(BaseModel):
    """A short, tool-agnostic retest goal — the steps the agent works to (FR-17 6b-ii).

    A few concise natural-language verification steps for any finding, not HTTP
    probes (the batch probe plan retired with the batch path in FR-17 6b-iii).
    """

    model_config = ConfigDict(frozen=True)

    steps: tuple[str, ...] = Field(default=(), max_length=6)


_GOAL_INSTRUCTIONS = """\
You turn a single pentest finding into a SHORT retest goal: an ordered list of a \
few (2-5) concise, tool-agnostic verification steps that say WHAT to confirm, not \
which tool to use. Make no assumption about the vulnerability class or protocol — \
describe re-exercising the reported condition and observing whether it still \
occurs. Keep each step to one short imperative line.
"""


def build_goal_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[None, GeneratedGoal]:
    """Build the retest-goal agent (FR-17 6b-ii): a generic, tool-agnostic goal generator.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the configured
            backend is used (``REVALID_LLM_MODEL``, FR-13); tests pass a stand-in.

    Returns:
        An agent whose validated output is a :class:`GeneratedGoal`.
    """
    return Agent(
        model if model is not None else resolve_model(),
        output_type=GeneratedGoal,
        instructions=_GOAL_INSTRUCTIONS,
        retries=_MAX_OUTPUT_RETRIES,
        defer_model_check=True,
    )


def _finding_prompt(finding: Finding) -> str:
    """Render the finding as the goal-generation prompt (the fields the model reads)."""
    endpoints = ", ".join(finding.affected_endpoints) or "(none stated)"
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(finding.reproduction_steps, 1))
    return (
        f"Title: {finding.title}\n"
        f"Severity: {finding.severity.value}\n"
        f"Description: {finding.description}\n"
        f"Attack vector: {finding.attack_vector}\n"
        f"Affected endpoints: {endpoints}\n"
        f"Reproduction steps:\n{steps or '(none stated)'}"
    )


def generate_goal(agent: Agent[None, GeneratedGoal], finding: Finding) -> tuple[str, ...]:
    """Generate a generic retest goal for ``finding`` (FR-17 6b-ii).

    Best-effort: on a model failure it returns an empty tuple so session start
    never blocks — the agent then falls back to the finding context alone.

    Args:
        agent: The goal agent (from :func:`build_goal_agent`).
        finding: The finding to derive a retest goal for.

    Returns:
        The generated goal steps, or ``()`` if the model produced nothing usable.
    """
    try:
        return agent.run_sync(_finding_prompt(finding)).output.steps
    except UnexpectedModelBehavior:
        return ()
