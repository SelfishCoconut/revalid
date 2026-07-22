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

## Update — 2026-07-21: the full per-change suite is now required

Writing up the CI gate for the thesis (methodology §3.5) surfaced a mismatch:
three per-change jobs ran on every PR but were **advisory** — `Frontend (lint,
types, build, tests)`, `Bandit (SAST)` and `Gitleaks (history scan)`. With
auto-merge, advisory means a red front-end build or a leaked secret could land
on `main` unblocked.

**Decision (Álvaro, 2026-07-21):** promote all three to required checks, so the
required set is now the *complete* set of per-change jobs:

```
Lint & types · Unit tests + coverage · Integration tests
Frontend (lint, types, build, tests) · pip-audit · Bandit (SAST)
CodeQL · Gitleaks (history scan)
```

Rationale: the gate is load-bearing precisely because no human reviews before
merge (see Consequences above), and the front end is now a first-class part of
the product (FR-11), not a side artefact. Accepted cost: scanner false
positives now block — as CodeQL's `incomplete-url-substring-sanitization` did on
PR #89. The standing policy is unchanged and applies to all four scanners: fix
the code or record an explicit, justified dismissal; never lower the threshold.

## Update — 2026-07-22: two new per-change jobs join the required set

A repository audit (issues #181/#182) found that `scripts/` — including the
`scripts/demo/*` programs the PR template makes the mandatory "How to validate"
evidence — sat outside every gate, and that the documentation build ran only
*after* merge, so PR #178 landed green and broke GitHub Pages for two commits.
PR #184 closed both holes by adding two CI jobs, `Offline demos` and
`Docs build (UML + mkdocs --strict)`.

That PR added the jobs but did not register them, so for a few hours they ran
without blocking — advisory checks in an auto-merge workflow, which is the exact
failure this ADR's previous update exists to prevent.

**Decision (Álvaro, 2026-07-22):** promote both, restoring the invariant that the
required set is the *complete* set of per-change jobs. Branch protection now
requires ten contexts:

```
Lint & types · Unit tests + coverage · Integration tests
Frontend (lint, types, build, tests) · Offline demos
Docs build (UML + mkdocs --strict) · pip-audit · Bandit (SAST)
CodeQL · Gitleaks (history scan)
```

Rationale unchanged from 2026-07-21, with one addition drawn from the audit: a
gate covers only what it is aimed at, and a job that runs without blocking is
aimed at nothing. The demo programs are the evidence every other claim of
correctness rests on, so leaving them advisory would have meant the validation
mechanism was the least-verified code in the repository.

Operational note for anyone editing these workflows: the required contexts are
matched by **exact job name**. Renaming a job — or introducing a `strategy.matrix`,
which suffixes the name (`CodeQL` becomes `CodeQL (python)`) — silently detaches it
from its required context, and the pull request then sits `BLOCKED` with every
check green. This happened on PR #184 and was caught before merge.
