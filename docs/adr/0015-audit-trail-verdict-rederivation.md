# 0015. Audit trail: verdicts re-derivable from stored evidence via a shared pure assessment

Date: 2026-07-14
Status: superseded by [ADR-0033](0033-retire-batch-execution-path.md) (batch execution path retired in full)

## Context

FR-10 (Must) requires a full audit trail: every system action persisted with
timestamp and actor, such that **any verdict can be re-derived from the trail
alone** — its acceptance criterion is a re-derivation routine that reproduces
every verdict from stored data only, no re-execution. NFR-02 (Must,
reproducibility) names the same test as its own.

The pieces are already close. A `VerdictRecord` persists the full `evidence`
(request/response) the verdict was derived from, plus its `plan_id`/
`plan_version` linkage. Plans store `created_at`, `origin`, `decided_at`/
`decided_by`; reports store `created_at` and the extraction `model`. Two gaps
remain for FR-10:

1. The *verdict* row has no timestamp or actor — it records what was decided but
   not *when* or *by whom* (the executor).
2. The assessment that turns evidence into a verdict lived inside `run_probe`,
   entangled with the network call, so there was no way to reproduce a verdict
   without re-executing a probe.

The insight that makes re-derivation cheap and trustworthy: **a verdict is a
pure function of its stored evidence.** The assessment logic and the FR-08 sanity
review take no input beyond the evidence and are deterministic, so re-running
them over stored evidence must reproduce the stored verdict — unless the logic
itself has changed since.

## Decision

We will make verdict re-derivation a first-class, tested routine backed by a
single shared assessment function.

- **Extract `retest.assess_evidence(probe_kind, evidence) -> Verdict`**, a pure
  function (a `response_status` of 0 marks an unreachable target). Both the live
  path (`run_probe` = execute → `assess_evidence`) and re-derivation call it, so
  they cannot drift.
- **Add `src/revalid/audit.py`.** `rederive_verdict(probe_kind, evidence)` =
  `review_verdict(assess_evidence(...))` — the non-network half of the FR-08
  `guarded_run`. `rederive_run(session)` recomputes every stored verdict from its
  evidence, diffs it against storage, and returns an `AuditReport`
  (`total` / `reproduced` / `discrepancies`). A clean run proves FR-10/NFR-02; a
  discrepancy flags a verdict whose assessment logic has since changed.
- **Complete the verdict audit record:** `VerdictRecord` gains `created_at`
  (server timestamp) and `actor` (default `executor`), so each verdict row is
  self-describing (when + who), matching the actor vocabulary user / model /
  executor already carried by reports (user upload, `model` extraction) and
  plans (`origin`, `decided_by`).
- **Expose it:** `GET /api/audit` returns the report (read-only), and
  `make demo-audit` shows a verdict stored from a live retest then reproduced
  from stored evidence alone.

**Scope.** FR-10's acceptance and NFR-02's stated test are *verdict*
re-derivation, which depends only on evidence — not on any LLM call. So this ADR
delivers that end-to-end and completes the verdict's timestamp/actor. NFR-02 also
asks that prompts and parameters be recorded per LLM call; the model name is
already persisted (report/plan `raw`, ADR-0009/0011), but full prompt/parameter
capture is a **separate follow-up**, tracked with FR-10, and does not affect
verdict re-derivability.

## Alternatives considered

- **Re-run probes to reproduce verdicts.** Rejected: the AC explicitly forbids
  re-execution, and re-running is non-deterministic (the target may have changed)
  and unsafe. Re-derivation from stored evidence is deterministic and offline.
- **Snapshot each verdict's decision inputs separately** (store the matched
  branch, thresholds, etc.). Rejected as redundant: the evidence *is* the input,
  and a shared pure `assess_evidence` is the logic — storing a second copy of the
  decision would be a drift risk, not a safeguard.
- **A dedicated `audit_log` table appending every action.** Rejected for now as
  over-built: the existing per-entity rows (report/finding/plan/verdict) already
  carry timestamps and actors and fully reconstruct a run. A separate append-only
  log can come with the FR-12 export if the evaluation needs it.

## Consequences

- **Easier:** every verdict is provably reproducible from storage (FR-10 AC /
  NFR-02) with one offline routine; the same `assess_evidence` guarantees the
  live and audit paths never diverge; re-derivation doubles as a regression
  detector when assessment logic evolves.
- **Harder / accepted debt:** NFR-02's per-LLM-call prompt/parameter capture is
  deferred (model name only, for now). Adding `created_at`/`actor` columns means
  pre-existing dev databases need recreation (SQLite `create_all`, no
  migrations) — acceptable for a single-user local tool (ADR-0008).
- `run_probe` no longer owns assessment; the unreachable case is now expressed
  as status-0 evidence flowing through the same `assess_evidence`.
