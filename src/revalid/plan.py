"""Retest-plan generation from findings (FR-04, ADR-0011).

Turns a :class:`~revalid.domain.Finding` into a :class:`~revalid.domain.RetestPlan`
of typed, non-destructive HTTP probe actions. A Pydantic AI agent *proposes* a
``list[PlannedAction]`` from the finding's reproduction steps (ADR-0002 stack,
ADR-0009 schema-gate pattern; model chosen by ``REVALID_LLM_MODEL`` — FR-13);
:func:`generate_plan` then *gates* those proposals through code the model cannot
influence: each action's target is bound to the allowlisted base URL and checked
against the FR-06 :class:`~revalid.allowlist.TargetGuard`, and only
non-destructive HTTP methods survive. Anything else is dropped and recorded,
never executed — so a plan references only authorized targets by construction
(FR-04 AC1), regardless of what the model proposed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import KnownModelName, Model

from revalid.allowlist import TargetGuard, canonicalize
from revalid.domain import Finding, Probe, RetestPlan
from revalid.llm import agent_model_name, resolve_model
from revalid.retest import GENERIC_KIND, classify_finding_kind, classify_probe_kind

_MAX_OUTPUT_RETRIES = 3

# Verification-only verbs. POST is included because some retests read a result
# back (e.g. the Juice Shop login-bypass), which is non-destructive; state-
# changing verbs are dropped so a plan cannot damage the target (FR-04, ADR-0011).
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "POST"})

_INSTRUCTIONS = """\
You turn a single penetration-test finding into a retest plan: an ordered list
of typed, non-destructive HTTP actions that would show whether the vulnerability
is still present. Work only from the finding's reproduction steps and affected
endpoints — never invent endpoints not implied by them.

For each action set:
- method: the HTTP verb. Use only GET, HEAD, OPTIONS, or POST — never a
  state-changing or destructive verb (no PUT, PATCH, DELETE).
- target: the endpoint path (e.g. /rest/user/login) or full URL to request.
- headers: request headers as a mapping (empty if none).
- json_body: a JSON object body, or null for a body-less request.
- expected_indicator: what an observed *still-open* result looks like for this
  action (e.g. "HTTP 200 with an authentication token"). Always state one.
- kind: the vulnerability class this action checks, as a short slug — one of
  access-control (IDOR/BOLA, missing authentication, admin access),
  sensitive-file-exposure (path traversal, backup/config files), or
  sqli-login-bypass (SQL-injection login bypass). Leave empty if none clearly fits.

Prefer a relative path for target (e.g. /rest/basket/2); the system supplies the
authorized host, so never copy a hostname from the report.

Keep actions verification-only: observe whether the issue is present, never
exploit it for real impact or modify data. Return the actions in execution
order.\
"""


class PlannedAction(BaseModel):
    """One action exactly as the model must propose it — the FR-04 typing gate.

    Only typed HTTP fields exist, so the model cannot emit a free-form command
    (FR-04 AC1). ``target`` is an *intent* (a path or URL); :func:`generate_plan`
    resolves and allowlist-checks it before it becomes a runnable
    :class:`~revalid.domain.Probe`. ``expected_indicator`` is required (AC2).
    """

    model_config = ConfigDict(frozen=True)

    method: str = Field(min_length=1)
    target: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    expected_indicator: str = Field(min_length=1)
    kind: str = Field(default="")
    """Lenient technique-class hint (ADR-0019); code normalizes it — see ``_gate``."""


class RejectedAction(BaseModel):
    """A proposed action the deterministic gate dropped — kept for audit.

    Attributes:
        action: The action as proposed by the model.
        reason: Machine-readable drop reason (``not_allowlisted`` /
            ``unsafe_method`` / ``invalid_target``).
    """

    model_config = ConfigDict(frozen=True)

    action: PlannedAction
    reason: str


class PlanResult(BaseModel):
    """Outcome of planning one finding.

    Attributes:
        plan: The retest plan holding only gated, allowlisted actions.
        rejected: Proposed actions dropped by the gate, with reasons.
        error: Non-empty when the model's output never passed the schema gate,
            in which case ``plan`` has no actions (nothing is guessed).
    """

    model_config = ConfigDict(frozen=True)

    plan: RetestPlan
    rejected: tuple[RejectedAction, ...] = ()
    error: str = ""


def build_plan_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[None, list[PlannedAction]]:
    """Build the retest-plan agent.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the
            configured backend is used (``REVALID_LLM_MODEL``, Claude by
            default — FR-13); tests pass ``TestModel``/``FunctionModel``.

    Returns:
        An agent whose validated output is a list of :class:`PlannedAction`.
    """
    return Agent(
        model if model is not None else resolve_model(),
        output_type=list[PlannedAction],
        instructions=_INSTRUCTIONS,
        retries=_MAX_OUTPUT_RETRIES,
        defer_model_check=True,
    )


def _finding_prompt(finding: Finding, instructions: str = "") -> str:
    """Render the finding as the planning prompt (fields the model plans from).

    ``instructions`` is optional free-text operator guidance for *this* generation
    (e.g. "also check /admin for IDOR"); it is appended as a clearly-labelled
    section so the model treats it as steering, not as finding content. It only
    biases what the model *proposes* — every proposal still passes the unchanged
    FR-06 gate in :func:`generate_plan`, so guidance cannot widen the target set.
    """
    endpoints = ", ".join(finding.affected_endpoints) or "(none stated)"
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(finding.reproduction_steps, 1))
    prompt = (
        f"Title: {finding.title}\n"
        f"Severity: {finding.severity.value}\n"
        f"Description: {finding.description}\n"
        f"Attack vector: {finding.attack_vector}\n"
        f"Affected endpoints: {endpoints}\n"
        f"Reproduction steps:\n{steps or '(none stated)'}"
    )
    if instructions.strip():
        prompt += f"\n\nAdditional operator instructions for this retest:\n{instructions.strip()}"
    return prompt


def gate_actions(
    actions: Iterable[PlannedAction],
    guard: TargetGuard,
    base_url: str,
    default_kind: str = GENERIC_KIND,
) -> tuple[list[Probe], list[RejectedAction]]:
    """Split proposed actions into gated probes and audited rejections (FR-04/FR-06).

    The single allowlist/method gate for both model-generated (FR-04) and
    user-edited (FR-05) actions: each action is resolved against ``base_url`` and
    checked against ``guard``; only non-destructive methods on allowlisted targets
    survive. Dropped actions are returned with a machine-readable reason.

    Args:
        actions: The proposed actions to gate.
        guard: The FR-06 allowlist guard — the sole authority on allowed targets.
        base_url: Allowlisted base URL that relative targets resolve against.
        default_kind: Technique kind assigned when an action carries no usable
            ``kind`` hint (ADR-0019); the caller derives it from the finding.

    Returns:
        A ``(probes, rejected)`` pair: runnable probes and audited rejections.
    """
    probes: list[Probe] = []
    rejected: list[RejectedAction] = []
    for item in actions:
        outcome = _gate(item, guard, base_url, default_kind)
        if isinstance(outcome, Probe):
            probes.append(outcome)
        else:
            rejected.append(RejectedAction(action=item, reason=outcome))
    return probes, rejected


def generate_plan(
    agent: Agent[None, list[PlannedAction]],
    finding: Finding,
    guard: TargetGuard,
    base_url: str,
    instructions: str = "",
) -> PlanResult:
    """Generate a gated retest plan for ``finding`` (FR-04).

    The model proposes actions; this function enforces the safety properties in
    code: every action's target is resolved against ``base_url`` and checked
    against ``guard`` (FR-06), and only :data:`SAFE_METHODS` survive. Dropped
    actions are recorded in :attr:`PlanResult.rejected`, never run.

    Args:
        agent: The plan agent (from :func:`build_plan_agent`).
        finding: The finding to retest.
        guard: The FR-06 allowlist guard; the sole authority on allowed targets.
        base_url: Allowlisted base URL that relative targets resolve against.
        instructions: Optional operator guidance for this generation, steered into
            the prompt and recorded in the plan's lineage. It cannot bypass the
            gate — proposals it elicits are gated identically (NFR-02 audit).

    Returns:
        A :class:`PlanResult`. On a schema-gate failure the plan has no actions
        and :attr:`PlanResult.error` explains why (nothing is guessed).
    """
    model_name = agent_model_name(agent)
    try:
        proposed = agent.run_sync(_finding_prompt(finding, instructions)).output
    except UnexpectedModelBehavior as exc:
        return PlanResult(plan=_empty_plan(finding, model_name, instructions), error=str(exc))

    default_kind = classify_finding_kind(finding)
    actions, rejected = gate_actions(proposed, guard, base_url, default_kind)

    plan = RetestPlan(
        finding_title=finding.title,
        actions=tuple(actions),
        raw={
            "source": "plan_generation",
            "model": model_name,
            "base_url": base_url,
            "finding_title": finding.title,
            "proposed": len(proposed),
            "rejected": len(rejected),
            "instructions": instructions,
        },
    )
    return PlanResult(plan=plan, rejected=tuple(rejected))


def _gate(
    action: PlannedAction, guard: TargetGuard, base_url: str, default_kind: str
) -> Probe | str:
    """Return a runnable :class:`Probe`, or a machine-readable drop reason.

    Enforces, in order: a non-destructive method, a resolvable target, and the
    FR-06 allowlist. The model's proposal is untrusted for targets (like report
    content), so an off-allowlist URL is dropped here — it never runs. A survivor
    is tagged with its technique ``kind`` (ADR-0019): the model's lenient hint
    normalized by :func:`~revalid.retest.classify_probe_kind`, falling back to
    ``default_kind`` — the routing that selects the assessor and renderer.
    """
    if action.method.upper() not in SAFE_METHODS:
        return "unsafe_method"
    try:
        url = canonicalize(urljoin(base_url, action.target))
    except ValueError:
        return "invalid_target"
    if not guard.is_allowed(url):
        return "not_allowlisted"
    return Probe(
        kind=classify_probe_kind(action.kind, default_kind),
        method=action.method.upper(),
        url=url,
        headers=action.headers,
        json_body=action.json_body,
        expected_indicator=action.expected_indicator,
    )


def _empty_plan(finding: Finding, model_name: str, instructions: str = "") -> RetestPlan:
    """A no-action plan recording that generation failed the schema gate."""
    return RetestPlan(
        finding_title=finding.title,
        raw={
            "source": "plan_generation",
            "model": model_name,
            "finding_title": finding.title,
            "instructions": instructions,
        },
    )
