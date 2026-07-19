# 0033. Retire the batch execution path (FR-17 Slice 6b-iii)

Date: 2026-07-19
Status: proposed

## Context

The agentic retest console (ADR-0025 → ADR-0032) now covers the whole retest
lifecycle end to end: a user-owned goal (ADR-0032) drives a Pydantic AI agent that
runs gated commands in an egress-locked sandbox, concludes a verdict (ADR-0030)
backed by flexible command-output evidence (ADR-0031), and leaves a replayable
transcript for the FR-10 audit and the FR-12 export.

That makes the original **batch execution path** fully redundant. The batch path
was: an LLM-proposed, deterministically-gated retest *plan* of typed HTTP `Probe`s
(ADR-0011), a server-side plan-approval gate (ADR-0012), an independent execution
sanity checker (ADR-0014), an optional browser-driven probe executor (ADR-0018),
and a kind-keyed technique registry (ADR-0019). Every one of those now has an
agentic equivalent, and keeping both means two verdict producers, two evidence
shapes, and a polymorphic `VerdictRecord` (ADR-0030's `source` discriminator)
whose **batch branch is never written** in an agentic-only world.

Two paths to the same outcome differ only in cost. Álvaro chose **full deletion
(clean end-state)** over a soft-deprecate: dead code is the project's #1
development pathology (CLAUDE.md), and a never-filled batch branch invites bit-rot
in exactly the audit/export surface the thesis leans on for reproducibility.

## Decision

Delete the batch execution path outright and collapse every shape it left
polymorphic down to the single agentic one.

1. **Delete the batch modules** and their demos/tests: `approval.py`, `retest.py`,
   `sanity.py`, `browser.py` (with `test_approval*`, `test_retest`, `test_sanity`,
   `test_browser`, `test_techniques`, `test_db_plan`, the batch API/pipeline tests,
   and the `demo-plan/approval/sanity/techniques/browser-xss/walking-skeleton`
   demos + Makefile targets).
2. **Remove the batch REST surface** from `app.py` (plan generate/edit/approve/
   reject/revise/list and the `retest` endpoints). Keep `GET /verdicts` and
   `GET /audit`, now served agentic-only.
3. **Strip `plan.py` to goal-only** — keep the repurposed `generate_goal` (FR-04,
   ADR-0032); drop `generate_plan`, `Probe` gating, and the allowlist coupling.
4. **Collapse the domain**: remove `Probe`, `RetestPlan`, `PlanStatus`, `Verdict`,
   and `Evidence`; keep `AgenticEvidence` and `VerdictStatus`.
5. **Collapse persistence**: drop `PlanRecord`; `VerdictRecord` becomes
   agentic-only — the `source` discriminator and the batch-only columns
   (`plan_id`, `plan_version`, `probe_kind`) go, leaving the single `agentic()`
   shape. This realizes ADR-0030's polymorphism as one concrete shape.
6. **Narrow the FR-12 export**: drop `PlanExport` and the `plans` metric;
   `VerdictExport` is agentic-only; schema **1.3 → 1.4** (published schema
   regenerated, drift test enforces it).
7. **Collapse the FR-10 audit** to agentic-only transcript re-derivation
   (`_rederive_agentic`); the batch evidence-rederivation branch is removed.
8. **Drop FR-14** (browser-driven probes) entirely.
9. **FR-06 unchanged, one mechanism.** Egress control now lives solely in the
   sandbox's Docker `--internal` network membership (ADR-0025) — strictly stronger
   than the deleted HTTP-transport allowlist, which only ever guarded batch
   `Probe`s. `allowlist.py` is thereby orphaned; it is **kept this slice** (a
   tracked cleanup follow-up) rather than deleted, to avoid expanding a
   batch-retirement slice onto the FR-06 module. `lab_base_url()` (the one helper
   the system test still used from `retest.py`) moves to `sandbox.py`.

**Implementation split.** 6b-iii-a is the **backend** deletion (this ADR's
realization). 6b-iii-b reshapes the **SPA** finding flow to Extract → Goal →
Agentic retest → Verdict.

## Alternatives considered

- **Soft-deprecate — keep the batch modules dead behind a flag.** Rejected: dead
  code is the #1 pathology here, and a `VerdictRecord.source` branch that is never
  written rots silently in the audit/export path.
- **Keep the batch path as a non-interactive fallback.** Rejected: free-launch
  mode (ADR-0029) already runs the agentic console unattended, so a second verdict
  producer buys nothing and doubles the FR-10/FR-12 surface to keep correct.
- **Delete `allowlist.py` in this slice too.** Rejected (deferred): it is an FR-06
  module, not part of the batch *execution* path proper; the CI gate does not force
  it (vulture is advisory, `--min-confidence 80 || true`), so its removal is a
  separate, safely-deferrable cleanup rather than scope creep here.
- **A soft schema bump (additive-only).** Rejected: the batch fields are gone, not
  optional; a clean `1.4` that matches the emitted document beats a schema carrying
  fields nothing writes.

## Consequences

- **Good:** one verdict producer (agentic), one evidence shape
  (`AgenticEvidence`), one audit path, one export shape. Nine source/test/demo
  modules deleted; `VerdictRecord`, `VerdictExport`, and the audit collapse from
  polymorphic to a single concrete shape.
- **Requirements:** FR-04/05/07/08 move *implemented → superseded by FR-17*; FR-14
  is *dropped*; FR-06 stays satisfied (network isolation); FR-09/10/12 now have
  exactly one implementation. ADR-0025 becomes ratifiable — its "supersedes
  FR-04/05/07-09 over time" is now realized — and ADRs 0011/0012/0014/0015/0018/
  0019/0022/0023 are superseded by this one.
- **Breaking export change (1.4):** consumers lose `plans` and the batch verdict
  fields. The only consumer, the FR-15 eval harness, already reads the agentic
  shape, so no migration is needed.
- **Accepted debt:** `allowlist.py` is orphaned pending a cleanup follow-up. The
  SPA still calls the removed batch endpoints until 6b-iii-b — they 404 harmlessly
  and the SPA build/tests mock the client, so the frontend is unaffected by this
  backend deletion.

## References

- Design spec: `docs/superpowers/specs/2026-07-19-agentic-retest-console-slice-6b-iii-design.md`
- Plan: `docs/superpowers/plans/2026-07-19-agentic-retest-console-slice-6b-iii-a.md`
- Supersedes ADR-0011, ADR-0012, ADR-0014, ADR-0015, ADR-0018, ADR-0019, ADR-0022,
  ADR-0023. Realizes ADR-0025; builds on ADR-0030/0031/0032. Epic
  [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue
  [#110](https://github.com/SelfishCoconut/revalid/issues/110).
