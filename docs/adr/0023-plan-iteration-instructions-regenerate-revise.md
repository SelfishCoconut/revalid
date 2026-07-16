# 0023. Plan iteration: operator instructions + regenerate / revise (go back a step)

Date: 2026-07-16
Status: proposed

## Context

The retest-plan lifecycle (FR-04 generation → FR-05 approve/execute) was
effectively **one-directional** in the UI. Once a plan was generated the operator
could edit or approve it; once **approved**, the action fields were locked and the
only forward move was *Run retest* — there was no way to go back and change course.
And generation took the finding as-is: the operator could not tell the model
"while you're at it, also check X for this finding."

During live testing Álvaro asked for two things, and confirmed the scope in a
design review:

- **Guidance at planning time.** Free-text instructions for a specific finding
  (e.g. "also check /admin for IDOR"), steering what the model proposes.
- **Go back to previous phases, at any point, without losing history.**
  Regenerate a plan, edit it, or **un-approve** it — with every prior version kept
  as an immutable audit record (he explicitly values the kept history).

Forces:

- The plan lifecycle is **versioned and append-mostly** (ADR-0012): a version is
  immutable except to record its own decision or be superseded. Any "go back" must
  fit that model — new versions, never destructive edits.
- The **FR-05 execution gate** must stay intact: nothing runs unless the finding
  has a live *approved* version, and `approved_plan()` must never point at a plan
  the operator has walked away from.
- The **FR-10 audit trail** must stay intact: going back must never delete
  verdicts (they are evidence).
- Guidance is **operator-supplied free text** — it must not become a way to widen
  the target set past the FR-06 allowlist.
- **Scope stays human-validated** (ADR-0019): this is about iterating on the
  *plan*, not letting the model rewrite the finding's scope/targets.

## Decision

Add three iteration moves, all expressed through the existing versioned model, and
keep all history.

- **Operator instructions (FR-04).** `POST /api/findings/{id}/plan` accepts an
  optional `{instructions}` body. The text is appended to the generation prompt as
  a clearly-labelled "operator instructions" section and recorded in the plan
  version's lineage (`raw.instructions`, stored up front on the reserved
  `generating` row so it survives even a failed settle). It only biases what the
  model *proposes* — every proposal is gated identically by FR-06, so guidance
  **cannot** introduce an off-allowlist or unsafe-method probe. The SPA shows an
  optional guidance box wherever a plan can be (re)generated, and surfaces the
  applied guidance on the resulting plan.

- **Regenerate = generate again, superseding any live version.**
  `start_plan_generation` now supersedes the finding's **live** versions —
  `generating`, `proposed`, **and `approved`** — before reserving the new
  `generating` row (ADR-0022 already made generation async). So the operator can
  discard and regenerate at *any* point; after a regenerate from an approved plan,
  `approved_plan()` returns `None` and a retest is refused (409) until the fresh
  plan is re-approved. This is the safe choice: the gate is never left pointing at
  a superseded plan while the UI shows a new one.

- **Revise = un-approve into an editable draft.**
  `POST /api/findings/{id}/plan/revise` (→ `revise_plan`) supersedes the approved
  version and re-proposes its already-gated probes as a new `proposed` version the
  operator can edit and re-approve. Returns 409 when there is no approved plan. The
  approved version stays in history as `superseded`; nothing runs until
  re-approval.

- **History is never destroyed.** Superseded/rejected/failed versions remain in the
  plan history; **verdicts are never deleted** — a retest after re-planning appends
  new verdicts stamped with the new plan version (FR-10 unchanged).

- **The pipeline circles are the go-back control (FR-11, #78).** The status track
  (extract → plan → approve → retest → verdict) that a finding already reads as
  "where am I" doubles as a stepper: a *reached, earlier* stage becomes a clickable
  button that steps back to it — `extract` opens the report, `plan` discards &
  regenerates, `approve` un-approves (revise). Each mutating step confirms first
  (a stray click must not throw work away); `retest`/`verdict` and not-yet-reached
  stages stay inert (recorded outcomes can't be un-happened — history is kept). The
  buttons drive the exact same operations above, so no new backend surface.

This is scoped as an **enhancement to FR-04/FR-05** (with the clickable-track
affordance under FR-11), not a new SRS requirement; this ADR is the design of
record. It traces to issues #73 (instructions), #74 (regenerate/revise), and #78
(clickable pipeline stages).

## Alternatives considered

- **Un-approve by flipping the approved row back to `proposed` in place.** Simpler,
  one row. Rejected: it mutates a *decided* version, breaking the ADR-0012
  invariant (a version is immutable except to record its own decision / be
  superseded) and losing the "this was approved then revised" trail. Re-proposing a
  copy keeps the history honest.
- **Regenerate that leaves an existing approved version untouched.** Rejected: then
  `approved_plan()` would still return the old approved plan while the UI shows a
  new proposal — a retest would silently run the *stale* plan. Superseding the
  approved version on regenerate keeps storage and UI consistent and fails closed.
- **Let "go back" clear verdicts / reset the finding.** Rejected: verdicts are
  FR-10 evidence; deleting them breaks the audit trail. History is append-only.
- **Instructions as a persistent per-finding field / editable system prompt.**
  Rejected for now: per-generation free text (recorded in lineage) is the simplest
  honest model and matches "guidance for *this* run"; a sticky default is a possible
  follow-up.
- **Let guidance also edit scope/targets.** Rejected: scope stays human-validated
  (ADR-0019); guidance steers proposals but the FR-06 gate is unchanged, so it
  cannot authorize new targets.

## Consequences

- **Easier:** the operator can steer planning with plain-language guidance, and can
  step back — regenerate, edit, or un-approve — at any point, with the full version
  history preserved. The loop matches the human-in-the-loop direction (ADR-0019).
- **Harder / accepted:** regenerating from an approved plan discards the approval
  (must re-approve before running) — intended, and recoverable via history. If a
  regenerate from approved then *fails* generation, the finding is left with no
  approved plan until the operator regenerates again; acceptable, since the
  operator explicitly chose to discard. Plan history grows more (revised/superseded
  versions), which is honest audit but noisier.
- **Safety:** guidance is untrusted free text but is gated identically to any
  proposal (FR-06), and `approved_plan()` is never left pointing at a superseded
  version, so the FR-05 execution gate holds throughout.
- **Route split:** adding the `revise` route pushed the plan/retest route
  registrar past the mccabe gate, so `_register_plan_and_retest_routes` was split
  into `_register_plan_routes` + `_register_retest_routes` (no behaviour change).
- **Status `proposed`:** the new lifecycle moves and the "regenerate supersedes an
  approved plan" behaviour are Álvaro's to ratify.
