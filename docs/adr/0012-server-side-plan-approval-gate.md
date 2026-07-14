# 0012. Server-side plan approval gate: versioned plan rows, single execution chokepoint

Date: 2026-07-14
Status: proposed

## Context

FR-05 requires that no retest plan executes without human approval, and that
edits to a plan are versioned with the executed version recorded in the audit
trail. Before this decision, FR-04 (`plan.py`, ADR-0011) produced a
`RetestPlan` that lived only in memory — no `PlanRecord`, no plan endpoints,
`RetestPlan.version` unused — while execution (`POST /findings/{id}/retest`)
ignored plans entirely and hardcoded the M1 SQLi probe. So "no plan executes
without approval" was not yet a real guarantee anywhere in the code, even
though ADR-0002 had already fixed the plan → human-approve → execute model
and SQLite persistence this decision now implements end to end.

Two acceptance criteria fix the shape of the gate:

- **AC1** — unapproved plans are not executable through *any* code path,
  enforced server-side, not only in the UI.
- **AC2** — plan edits are versioned; the executed version is recorded in the
  audit trail.

FR-05 lands before the React UI (FR-11, #16) exists. Like FR-06 before the
executor, it must ship the gate and the seam the UI is later forced through,
not a UI-side convention that a future client could bypass.

## Decision

Approval is enforced by **construction of the call graph**, not by a rule the
caller must remember: plans are persisted as **versioned rows**, and exactly
**one function** stands between stored plans and the network — persisting
and gating the `RetestPlan`/`Probe` that FR-04 (ADR-0011) produces.

- **Versioned plan rows.** A new `plans` table holds one row **per version**
  (append-only; a row is mutated only to record its own decision or to be
  marked `superseded`, never rewritten). Each row carries `finding_id`,
  `version`, `status`, `origin` (`generated`/`edited`), the gated `actions`
  and `rejected_actions`, `raw` lineage, and decision metadata
  (`created_at`/`decided_at`/`decided_by`). A `PlanStatus` `StrEnum`
  (`PROPOSED`/`APPROVED`/`REJECTED`/`SUPERSEDED`) joins `domain.py`.
- **State machine: ≤1 `proposed` and ≤1 `approved` live per finding.**
  Generating or editing a plan supersedes any existing `proposed` row and
  inserts a new one at `version = max+1`; a prior `approved` row is left alone
  (editing does not silently unapprove the runnable version). Approving the
  latest `proposed` row supersedes any prior `approved` row. Rejecting the
  latest `proposed` row marks it `rejected`. `approve`/`reject` with no
  `proposed` row raises `NoProposedPlanError`. This is the minimal lifecycle
  that supports approve / reject / edit / regenerate without ambiguity about
  which version runs.
- **`execute_approved_plan` is the single execution chokepoint (AC1).** It is
  the *only* function in the codebase that turns a persisted plan into HTTP
  traffic: it looks up the finding's single `approved` row and, if none
  exists, raises `PlanNotApprovedError` **before opening a socket**; otherwise
  it runs each of that version's probes through the existing FR-06/FR-07
  transport and persists one `VerdictRecord` per probe. The FastAPI retest
  endpoint (`POST /findings/{id}/retest`, now returning `list[VerdictOut]`)
  calls it and nothing else does — so AC1 is an invariant of the call graph,
  not a convention the UI (or any future client) has to honor. The system
  test seeds the real M1 `login_sqli_probe` (`sqli-login-bypass`) as an
  approved plan (`test_approved_plan_retest_still_open_via_api`), proving
  `still_open` end-to-end instead of a special-cased code path; the demo
  (`scripts/demo/approval_gate.py`) instead drives generate/edit/approve/
  retest through the endpoints with a stand-in `FunctionModel` agent,
  honestly reporting `inconclusive` for the generated `planned-http` probe.
- **Edited actions are re-gated, not trusted.** `plan.py`'s allowlist/method
  gate (`_gate`) is extracted into a reusable `gate_actions(actions, guard,
  base_url)`, used by both `generate_plan` (FR-04, behaviour unchanged) and
  the new `edit_plan`. A user-submitted edit is exactly as untrusted as a
  model-proposed action: an off-allowlist or destructive edit is dropped, not
  run — client input does not get a free pass just because ADR-0008 is a
  single-*trusted*-user threat model. If every submitted action is dropped,
  the edit endpoint raises `AllActionsRejectedError` (422) rather than
  persisting an empty plan.
- **Minimal audit now; FR-10 unifies it later.** Approval is recorded as
  `status`/`decided_at`/`decided_by` on the plan-version row itself, and the
  executed version is stamped onto the verdict row via new
  `VerdictRecord.plan_id`/`plan_version` fields (nullable, so the M1 hardcoded
  path and older rows stay valid). This satisfies AC2 — the executed version
  is recorded — without pulling the M4 `audit_events` table (FR-10) forward.
  The version rows *are* the approval history for now; FR-10 later unifies
  every event type into one re-derivable trail (NFR-02).
- **Assessment stays dispatched by probe kind, not generalized here.**
  `run_probe` looks up an assessor by `probe.kind`: `sqli-login-bypass` keeps
  its real `assess()`; any other kind (including FR-04's `planned-http`) falls
  through to `assess_generic`, an honest `inconclusive` verdict with
  `reason_code="no_assessor"`. Turning `expected_indicator` into a real
  matcher is explicitly **not** part of this decision.

## Alternatives considered

- **UI-enforced approval only (no server-side gate).** Rejected: AC1 requires
  the guarantee to hold "through any code path," which a UI check cannot
  provide — any future client, script, or demo could call the retest endpoint
  directly and run an unapproved plan. This is the same reasoning ADR-0011
  applied to the allowlist: the safety property must be structural.
- **Mutate a single plan row per finding in place, rather than append-only
  versions.** Rejected: AC2 requires edits to be versioned with the executed
  version recoverable from the audit trail. An in-place update would lose the
  history a reviewer (or FR-10) needs to answer "which version actually ran."
- **A separate `audit_events` table now, ahead of FR-10.** Rejected: it would
  pull M4 scope forward for a guarantee the version rows already satisfy
  (decision fields + `plan_version` stamp are enough to answer AC2 today).
  Building one now risks a shape FR-10 would just have to reconcile or
  replace once it unifies all event types.
- **A parallel edit-only code path that skips the FR-06 gate ("the user
  already reviewed it").** Rejected: it would create a second, ungated
  execution surface — exactly the kind of second door AC1 rules out. Reusing
  `gate_actions` for edits keeps there being only one gate, matching the
  "one chokepoint" principle applied to execution.
- **Generalizing verdict assessment to arbitrary `expected_indicator` strings
  as part of this change.** Rejected: that is FR-08/FR-09 scope (a
  match-rule/sanity-checker design of its own); FR-05 must not smuggle it in
  under a different requirement's ADR. `assess_generic`'s honest
  `inconclusive` keeps the boundary visible instead of guessing.
- **A rich per-action edit UI (diff view, batch approve) as part of this
  change.** Rejected: FR-05 is the server-side seam; the UI that drives it is
  FR-11 (#16). The versioned edit + approve endpoints are UI-agnostic on
  purpose so #16 calls them directly rather than inventing its own concept.

## Consequences

- **Easier:** AC1 is enforced by the call graph — there is one function that
  can run a stored plan, and it is unreachable without an `approved` row;
  AC2's "executed version recorded" is a plain foreign-key stamp on the
  verdict row; edits reuse the exact same gate as generation, so there is only
  one allowlist/method enforcement path to reason about (the #1 AI-development
  duplication failure mode per `CLAUDE.md`); the M1 hardcoded probe collapses
  into ordinary seeded data instead of a special case.
- **Harder / accepted debt:** the audit trail is minimal — per-version
  decision fields plus a verdict stamp, not a unified `audit_events` log —
  until FR-10 (M4) arrives; verdict assessment for non-`sqli-login-bypass`
  probes stays `inconclusive`/`no_assessor` until FR-08/FR-09 generalize
  indicator matching, so FR-05 cannot yet prove `still_open`/`fixed` for
  arbitrary generated actions; there is no rich edit UI yet (FR-11 provides
  the client), so today the edit endpoint is only exercised via API calls and
  the demo script; no optimistic locking on approve-vs-edit races, consistent
  with the single-user, single-process threat model (ADR-0008).
- **Status `proposed`:** the versioned-rows model, the single-chokepoint
  design, and the minimal-audit-now split from FR-10 are Álvaro's to ratify in
  async review, per the design dialogue recorded in
  `docs/superpowers/specs/2026-07-14-fr05-approval-gate-design.md`.
