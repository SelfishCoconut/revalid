# 0024. Finding revision & annotation; pipeline stage wizard

Date: 2026-07-16
Status: accepted

## Context

The clickable pipeline stepper (ADR-0023, #78) turned the finding-detail status
track (extract → plan → approve → retest → verdict) into a *stepper*: a reached,
earlier circle was a button that, on click, **confirmed then mutated** — `plan`
discarded & regenerated, `approve` un-approved. Live use exposed the problem
Álvaro raised in a design review (2026-07-16):

- **Click conflates navigation with mutation.** Clicking a stage to *look at it*
  raises a `window.confirm` and risks throwing work away. The operator wanted to
  "just move between the stages" — like the steps of a multi-step form — and only
  *then* decide to edit or regenerate.
- **No focused surface per stage.** Everything lived on one long scrolling page
  (`FindingDetail`): plan editor, verdicts, history all at once. There was no
  page *about* a single stage.
- **The extracted finding is frozen.** A `FindingRecord` is a single flat,
  mutable row. There is no way to correct the LLM's extraction (fix a wrong
  endpoint, sharpen a description) and no way to annotate reasoning — yet the
  operator is the human-in-the-loop who should be able to (ADR-0019).

Forces:

- **Append-only history is the house style.** Plans are versioned and immutable
  (ADR-0012); ADR-0023 kept every superseded version; verdicts are FR-10
  evidence and are never deleted. Any "edit the finding" must fit that model —
  new versions, never destructive edits — and any "leave a note" must not become
  a mutable field that loses its own history.
- **Findings are referenced by other aggregates.** `plans.finding_id` and
  `verdicts.finding_id` point at `findings.id`. Versioning findings must not
  orphan them.
- **No migration tool.** The schema is `Base.metadata.create_all`; dev resets by
  deleting `revalid.db`. Schema changes are additive tables, validated on a
  fresh DB.
- **FR-12 export is the audit-grade snapshot.** It must stay complete: if
  findings gain versions and notes, the export must carry them.

## Decision

Four changes, expressed through the existing append-only model, with all history
kept. This is the design of record for **FR-16** and the **FR-11** wizard
enhancement (issue #80).

### 1. The finding-detail view becomes a five-page stage wizard

- `/findings/:id` becomes a **layout route**: the finding identity header and the
  `PipelineTrack` stepper are pinned; the stage content renders in an `<Outlet/>`
  below. Five child routes — `/findings/:id/{extract|plan|approve|retest|verdict}`
  — each own one stage. `/findings/:id` redirects to the **current** (furthest
  actionable) stage, so opening a finding lands where the work is.
- **The pipeline circles become plain navigation.** A reached-or-current circle
  is a `<Link>` to that stage: it navigates and **nothing else** — no
  `window.confirm`, no mutation. Not-yet-reached stages stay inert.
- **Destructive/irreversible operations move onto their stage page as explicit
  buttons.** Plan → *Discard & regenerate*; Approve → *Un-approve / revise*;
  Retest → *Run retest*. They call the **exact same** ADR-0023 backend
  operations — no new plan surface. Editing the plan actions lives on Plan;
  approve/reject on Approve; verdicts render on Verdict.

This **supersedes ADR-0023's "each mutating step confirms first" affordance.** The
guarantee is now stronger, not weaker: because a click only ever *navigates*, a
stray click can never throw work away — the destructive act is a deliberate,
labelled button press on the stage that owns it, not a click-through on a dialog.

### 2. Findings become versioned, symmetric with the plan model (ADR-0012)

- A finding splits into a stable **identity** and append-only immutable
  **version** rows. The existing `findings` table is **retained as the identity**
  (`id`, `report_id`, `created_at`); a new `finding_versions` table holds every
  version of the editable content (`title`, `severity`, `description`, `impact`,
  `attack_vector`, `affected_endpoints`, `reproduction_steps`, `raw`) plus
  `version`, `origin` (`extraction` | `edit`), `created_at`, and edit lineage
  (`edited_by`, `reason`). Extraction, FR-02 import, and manual-report entry each
  create the identity row + **version 1** (`origin=extraction`). The **current**
  version is the highest `version` — the same "latest live version" rule plans
  already use.
- Because `findings.id` is preserved as the identity, **`plans.finding_id` and
  `verdicts.finding_id` keep pointing at it unchanged** — no FK re-pointing, no
  orphaning.
- `POST /api/findings/{id}` records the submitted fields as a **new immutable
  version** (`origin=edit`); a prior version is never mutated or deleted.
  `GET /api/findings/{id}/versions` returns the full ordered history.
  `GET /api/findings` and the finding views return the **current** version joined
  to the identity `id`.
- **Editing the extract does not invalidate downstream work.** Plan generation
  reads the current version; an existing plan/verdict already captured its own
  source finding in `raw` and is FR-10 evidence. The version history *is* the
  correction record (human-in-the-loop, ADR-0019) — not a trigger to delete
  plans or verdicts.

### 3. Per-finding, stage-tagged, append-only notes

- A `finding_notes` table: `id`, `finding_id` (→ identity), `stage` (one of the
  five stages, or `general`), `body`, `created_at`, `author`. `POST
  /api/findings/{id}/notes` (`{stage, body}`) appends a note; `GET
  /api/findings/{id}/notes` lists newest-first. Notes are **append-only** — never
  edited or deleted, so the reasoning trail is kept. UI: a notes thread in the
  finding header; each stage page shows and can add its own stage-tagged notes.

### 4. FR-12 export carries versions + notes

- `FindingExport` gains the finding's version history and its notes.
  `SCHEMA_VERSION` bumps; the published schema is regenerated (`make
  export-schema`) and the drift test keeps it honest. The export stays a complete
  audit snapshot (FR-12 unchanged in intent).

## Alternatives considered

- **Keep ADR-0023's confirm-on-click stepper.** Rejected: it conflates navigation
  with mutation, the operator found the popup obstructive, and a focused per-stage
  page makes mutation a deliberate act rather than a click-through — a stricter
  safety guarantee.
- **In-page tab state instead of sub-routes.** Rejected: no browser back/forward,
  no bookmarkable stage, a refresh loses your place. "A different page each" is
  literal and shareable with real routes.
- **Revisions side-table (mutable current `findings` row + history snapshots).**
  Considered — lowest blast radius — but rejected for **full immutable version
  rows**: symmetry with the plan model and a cleaner append-only guarantee (the
  current state is itself an immutable version, never mutated in place).
- **A brand-new `finding_identity` table with re-pointed `plans`/`verdicts`
  FKs.** Rejected as unnecessary churn: the existing `findings.id` already *is* a
  stable identity, so keeping `findings` as the identity table delivers the
  immutable-version-rows model **without** re-pointing any foreign key.
- **A single editable note, or one editable note per stage.** Rejected: editing
  overwrites, which loses the note's own history — against the kept-history house
  style. An append-only, stage-tagged log preserves the operator's reasoning.
- **Let a finding edit invalidate/rescind existing plans & verdicts.** Rejected:
  they captured their own source and verdicts are FR-10 evidence; the version
  history is the correction record, not a reason to delete downstream work.

## Consequences

- **Easier.** Each stage is a focused, bookmarkable page; moving between stages is
  always safe (no destructive clicks); the operator can correct the LLM's
  extraction with a kept audit trail and annotate reasoning per stage; the export
  stays a complete audit snapshot.
- **Harder / accepted.** A finding is now a two-table aggregate (identity +
  versions): every read resolves the current version, and extraction/import/
  manual-report write two rows. Export and audit read more. The schema change,
  with no migration tool, means a fresh DB in dev (already the practice). The
  wizard is more routes/components than the single page it replaces.
- **Safety / scope.** A finding edit never widens scope past FR-06 — planning
  re-gates every proposal regardless of the finding text (ADR-0019: scope stays
  human-validated). Notes are free text with no execution effect. History is
  never destroyed (FR-10 intact).
- **Supersession.** This retires only ADR-0023's confirm-on-click affordance; the
  rest of ADR-0023 (versioned iteration — regenerate, revise, operator
  instructions) stands unchanged and is precisely the backend these pages drive.
- **Follow-up (deferred).** Plans/verdicts could additionally stamp the
  `finding_version` they were derived from for even tighter audit; not needed now
  because `plan.raw` already snapshots the source finding.
