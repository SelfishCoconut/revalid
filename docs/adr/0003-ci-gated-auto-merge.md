# 0003. CI-gated auto-merge replaces the manual pre-merge validation gate

Date: 2026-07-01
Status: accepted

## Context

ADR-0001 established that nothing merges until Álvaro manually runs each PR's
"How to validate" steps. In practice this serialized all progress on a single
reviewer: green, low-risk PRs stalled waiting on a manual gate. The concrete
trigger was a security fix — a one-line `starlette` bump clearing a live CVE
(PYSEC-2026-249) — sitting mergeable-but-blocked while four Dependabot PRs and
two health-fix PRs queued behind the same gate.

The goal is an enterprise-standard flow. Real teams do not remove merge
controls; they replace *manual pre-merge validation* with *protected `main` +
required CI checks + auto-merge*, and move human review to an asynchronous,
post-merge activity backed by revert authority. That is both faster and, given
the required checks, safer than a hand-run checklist.

## Decision

We will adopt **CI-gated auto-merge**:

- A PR squash-merges automatically once the repository's **required** status
  checks pass. Branch protection stays on; `main` is never pushed directly.
- Claude may queue auto-merge (`gh pr merge --auto`); the capability is granted
  locally in `.claude/settings.local.json` (gitignored, not repo policy).
- `Validate` becomes Álvaro's **asynchronous** review after merge; he retains
  full revert authority.
- Branch protection (the concrete gate): `main` requires green **Lint & types,
  Unit tests, Integration tests, `pip-audit`, and CodeQL**. The strict
  *branches-up-to-date* requirement is **off**, so small non-overlapping PRs
  merge in parallel once their own checks pass, instead of serializing behind
  each other. Revisit with a GitHub merge queue if contributor/PR volume grows.
- Unchanged and still non-negotiable (§6): Álvaro owns all design decisions
  (recorded as ADRs), the `Co-Authored-By: Claude` trailer, data-protection
  rules, and the AI-usage declaration.

This amends only the merge step of ADR-0001; the rest of ADR-0001 stands.

## Alternatives considered

- **Keep manual pre-merge validation (ADR-0001).** Rejected: a single-reviewer
  bottleneck that stalls even green, trivial, or security-critical PRs — below
  enterprise throughput norms.
- **Remove controls / allow direct pushes to `main`.** Rejected: this is *below*
  enterprise standard, not above it. It discards CI gating and review entirely.
- **Require a human approving review (CODEOWNERS) before auto-merge.** Deferred:
  redundant for a single-author repo today; async review plus revert is
  sufficient. Can be added later without reversing this decision.

## Consequences

- **Easier:** green PRs land without waiting; security and dependency fixes flow
  promptly; throughput is no longer capped by reviewer availability.
- **Accepted debt / risk:** a flawed change can reach `main` before human eyes.
  Mitigated by required CI + revert authority — but this makes the *set* of
  required checks load-bearing. Accordingly, **`pip-audit` (Security) and CodeQL
  were added to the required checks** as part of this decision (previously only
  Lint/Unit/Integration blocked a merge), so auto-merge cannot land a vulnerable
  change — exactly the class of bug that motivated this ADR.
- **Authorship (§6): unchanged.** Ownership, attribution, and declaration
  obligations are untouched; only the *timing* of review moves from pre-merge
  block to post-merge async.
