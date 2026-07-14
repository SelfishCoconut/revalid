# 0011. Retest-plan generation: LLM-proposed typed actions, deterministically gated

Date: 2026-07-13
Status: accepted

## Context

FR-04 requires deriving, per finding, a **retest plan**: an ordered list of
**typed, non-destructive HTTP probe actions** with expected still-open/fixed
indicators, generated from the finding's reproduction steps. Acceptance:
(1) each action is a typed object — no free-form commands — referencing **only
allowlisted targets**; (2) each action states the indicator that would mark the
vulnerability present.

The pieces this builds on already exist:
- `domain.Probe` (FR-07) is already a typed, non-destructive HTTP action —
  `kind`, `method`, `url`, `headers`, `json_body`, `expected_indicator`.
- `allowlist.TargetGuard` (FR-06) is the sole authority on whether a URL is an
  authorized target, built only from trusted config — a report URL can never
  widen it.
- The FR-03 extraction agent (ADR-0009) established the pattern: an injectable
  Pydantic AI agent with a `list[…]` structured output gated by schema
  validation, model selected by `REVALID_LLM_MODEL` (FR-13/ADR-0010).

So the open question is only *how a plan is produced* on top of these.

## Decision

Plans are **LLM-proposed and deterministically gated**. A Pydantic AI agent
reads the finding and emits a `list[PlannedAction]`; the generator then binds
and filters those proposals through code the model cannot influence, and maps
the survivors to domain `Probe`s inside a `RetestPlan`.

- **`PlannedAction`** is the model's output schema and the FR-04 typing gate:
  `method`, `target` (a path or URL *intent*), `headers`, `json_body`,
  `expected_indicator` (non-empty — AC2). Only these typed HTTP fields exist,
  so the model **cannot** emit a free-form command (AC1). Invalid output is
  retried by Pydantic AI and, if still invalid, the finding yields an **empty
  plan flagged with the error** — never a malformed action (the ADR-0009 gate,
  reapplied).
- **`build_plan_agent(model=None)`** mirrors `build_extraction_agent`:
  `output_type=list[PlannedAction]`, fixed instructions, `defer_model_check=True`,
  model from `resolve_model()` (FR-13). Tests inject `TestModel`/`FunctionModel`.
- **`generate_plan(agent, finding, guard, base_url)`** is the deterministic
  gate. For each proposed action it (a) resolves `target` against the
  allowlisted `base_url` (relative → joined; absolute → as-is), (b) **enforces
  the FR-06 guard** — an action whose resolved URL is not allowlisted is
  **dropped**, not executed, and recorded as rejected, so the plan references
  only allowlisted targets *regardless of what the model proposed* (AC1, and
  consistent with FR-06 AC2), and (c) enforces a **non-destructive method
  allowlist** (`GET`, `HEAD`, `OPTIONS`, `POST`) — `DELETE`/`PUT`/`PATCH` are
  dropped. Survivors become `Probe`s; the result is a `RetestPlan`.
- **`RetestPlan`** (domain) is `finding_title`, an ordered `tuple[Probe, …]`,
  a `version` (1 at generation — FR-05 will bump on edit), and `raw` lineage
  (model name, source finding) for the audit trail (FR-10/NFR-02).
  `generate_plan` returns a `PlanResult(plan, rejected)` so dropped actions are
  auditable, never silent.

Reusing `Probe` (not a parallel "PlanAction") keeps one typed-action model
across planning and execution — the executor (FR-07) already runs `Probe`s.

## Alternatives considered

- **Deterministic, rule-based plan synthesis (no LLM).** Rejected: it cannot
  generalize across the open set of finding classes (SQLi, XSS, IDOR, traversal,
  OSINT…) from free-text reproduction steps; ADR-0002 fixed an LLM in the loop
  for exactly this interpretation step. The determinism belongs in the *gate*,
  not the *generation*.
- **Trust the model to only emit allowlisted, non-destructive actions
  (prompt-only).** Rejected: AC1 is a safety property and must be *enforced*,
  not requested. The guard and method allowlist make it structural — the model
  is untrusted for targets, exactly as report content is (FR-06).
- **A structured match-rule DSL for indicators now** (status/body/timing
  predicates). Rejected for FR-04: AC2 only requires each action to *state* its
  indicator, and a string does that. Machine-evaluated indicators are FR-08's
  (sanity checker) and FR-09's concern; over-building here would pre-empt those
  designs. `expected_indicator` stays a documented statement for now.
- **A new `PlanAction` domain type distinct from `Probe`.** Rejected:
  duplication of the executor's action model (the #1 AI-development failure mode
  we guard against); `Probe` already carries every field a planned action needs.

## Consequences

- **Easier:** AC1 (typed-only, allowlist-only) and non-destructiveness are
  enforced by construction, not convention; plans feed straight into the FR-07
  executor because actions *are* `Probe`s; the model switch (Claude/Ollama) is
  inherited from FR-13 for free; fully testable offline with `FunctionModel`.
- **Harder / accepted debt:** indicators are human-readable strings, so verdict
  matching for generated (non-login-SQLi) actions is still `assess`'s hardcoded
  logic until FR-08/FR-09 generalize it — FR-04 produces plans, not yet verdicts
  for arbitrary actions. Plan *persistence and versioning* land with FR-05; the
  `version` field is present but static (always 1) until then.
- **Known limitation — client-side hash routes:** the gate resolves each target
  with `urljoin` + `canonicalize`, which keeps path and query but **drops the URL
  fragment** (everything after `#`). A single-page-app route such as
  `/#/administration` therefore collapses to the base URL (`/`) — which *is*
  allowlisted, so it is not dropped but retests the server root instead of the
  intended view. This is inherent to HTTP-level probing: a `#`-fragment is a
  client-side concern the server never sees, so no HTTP request can exercise it.
  Findings that only manifest through DOM/hash-routed navigation are out of scope
  for FR-04 and belong to FR-14 (Playwright-driven probes, M5). The finding's
  *reproduction steps* still carry the route for a human reviewer, and FR-05's
  approval gate is where such a plan would be corrected or rejected before it runs.
- **Status `proposed`:** the LLM-proposes/code-gates split and the
  reuse-`Probe` call are Álvaro's to ratify in async review.
