# 0022. Asynchronous plan generation: a persisted `generating` version settled by a background job

Date: 2026-07-16
Status: superseded by [ADR-0033](0033-retire-batch-execution-path.md) (batch execution path retired in full)

## Context

FR-04 plan generation calls the LLM, which can take seconds to tens of seconds
(especially against a local Ollama model — the default backend since ADR-0021).
Until now `POST /api/findings/{id}/plan` was **synchronous**: it ran
`generate_plan` inline, persisted the proposed version, and returned it. Two
problems surfaced during live testing:

- **The UI state was not durable.** The "Generate plan" button drove a mutation
  whose only in-progress signal was React-local (`generate.isPending`). Reloading
  the page mid-generation threw that state away: the finding showed *no plan* and
  the button was clickable again, so the same finding could be generated twice and
  the operator could not tell whether a generation was already running. The state
  did not survive a reload because nothing was persisted until the call returned.
- **It is inconsistent with report ingestion.** Report upload (FR-01/ADR-0013)
  already solved exactly this: `POST /api/reports` returns `202` with a persisted
  `extracting` row, a background task runs the LLM, and the SPA polls the report
  until it settles on `ready`/`failed`. Plan generation — the other long LLM call
  in the pipeline — did it differently, so a reload behaved differently depending
  on which step you were on.

Forces:

- The in-progress state must **survive a reload** and be **single-authoritative**
  (one source of truth the server owns, not React-local).
- A finding must not have two concurrent live generations from a double-click.
- The plan lifecycle (FR-05/ADR-0012) is versioned and append-mostly: a version
  is immutable except to record its own decision or be superseded. Any new state
  must fit that model and keep the single execution chokepoint intact.
- The change should **reuse the proven report-ingest pattern**, not invent a
  second async mechanism.

## Decision

Make plan generation **asynchronous, mirroring report extraction**: the `POST`
reserves a persisted `generating` plan version and returns `202` immediately; a
FastAPI background task runs the LLM and **settles that same row in place** to
`proposed` (with its gated actions) or `failed` (with the reason). The SPA polls
`/plans` until it settles, exactly as it polls a report.

- **Two new `PlanStatus` states — `generating` and `failed`.** A version is born
  `generating` and always settles to `proposed` or `failed`, so the plan poll is
  guaranteed to terminate — the same lifecycle shape as `ReportStatus`
  (`extracting → ready/failed`). `PlanRecord` gains a nullable `error` column
  (set only on a `failed` version), mirroring `ReportRecord.error`.
- **`approval.py` owns the transition, in place.** `start_plan_generation` opens
  the version: it supersedes any live proposal (`proposed` **or** `generating`,
  so a re-generate cancels an in-flight one) and inserts a `generating` row with
  empty actions, returning it for the `202`. `finish_plan_generation(plan_id,
  result)` settles it: to `proposed` with the gated actions, or `failed` with the
  reason when the model produced nothing runnable. It is a **no-op if the row is
  no longer `generating`** (a newer generation superseded it), so a slow stale
  result can never resurrect a superseded version. Settling in place (same `id`,
  same `version`) means the poll transitions cleanly and history stays honest.
- **`app.py`: a `run_plan_generation` background task**, the structural twin of
  `run_extraction` — a sync function on Starlette's threadpool that opens its own
  session, calls `generate_plan`, and calls `finish_plan_generation`; any
  unexpected exception is converted to a `failed` settle so the row never gets
  stuck `generating`. The `create_plan` route becomes `status_code=202`, takes
  `BackgroundTasks`, and returns the reserved version. The former `422` for an
  empty/failed generation is **replaced by a persisted `failed` version** the UI
  renders (with a retry), consistent with how a failed extraction persists a
  `failed` report rather than erroring the upload.
- **Frontend: poll + render the two new states.** `usePlans` gains a
  `refetchInterval` that polls every 2 s while any version is `generating` and
  stops once it settles — the `useReport` pattern. `FindingDetail` branches on a
  new `currentPlan` selector (the newest *live* version — `generating`/`proposed`/
  `approved`/`failed`; superseded and rejected are history): a spinner panel while
  `generating`, the plan editor when `proposed`/`approved`, and a failure panel
  with the recorded error + "Try again" when `failed`. A reload therefore always
  shows the real server state.

This is scoped as an **implementation change to FR-04/FR-05** (generation becomes
async and gains the `generating`/`failed` states), not a new SRS requirement; this
ADR is the design of record.

## Alternatives considered

- **Keep it synchronous, add a client-side "generating" flag.** Minimal backend
  change. Rejected: the flag is React-local and dies on reload — precisely the bug.
  Only server-persisted state survives a refresh.
- **Insert a new proposed version on finish (don't settle in place).** The
  `generating` row would be superseded and a fresh `proposed` row inserted. Rejected:
  it burns a version number per generation and makes the poll watch one row appear
  while another is superseded, for no benefit over an in-place settle.
- **Don't persist failed generations (keep the `422`).** Rejected: it reintroduces
  the "nothing persisted, reload shows no plan" inconsistency for the failure path,
  and diverges from report ingestion, where a failed job persists a `failed` row the
  UI explains. A recorded `failed` version is also better lineage.
- **A generic background-job table / task queue (Celery, RQ, etc.).** Rejected:
  over-engineered for a single-user local tool (ADR-0008). FastAPI `BackgroundTasks`
  already runs report ingestion; reusing it keeps one mechanism.
- **WebSocket/SSE push instead of polling.** Rejected: polling already works for
  reports and is trivially correct across reloads; a push channel is complexity the
  tool does not need at this scale.

## Consequences

- **Easier:** the generate button's in-progress state is durable — a reload shows
  the finding still generating (or the settled plan/failure), and the same finding
  can't be double-generated because a live proposal/generation is superseded on
  re-generate. Generation and ingestion now behave identically, so the SPA's poll
  logic and mental model are one thing.
- **Harder / accepted:** `POST /plan` no longer returns the finished plan — callers
  (and tests) must poll `/plans` for the settled version, as they already do for
  reports. Two new lifecycle states widen the `PlanStatus` surface; `failed` and
  `generating` versions now appear in plan history (honest audit, slightly noisier).
- **Concurrency:** the in-place settle is guarded — `finish_plan_generation` only
  writes a row still `generating`, so a re-generate that supersedes an in-flight one
  cannot be clobbered by the older task's late result.
- **Runtime semantics:** an in-flight generation keeps the model it started with
  (the background task captured the agent at `202` time); a mid-generation settings
  change (ADR-0021) applies to the next generation. Consistent with report ingest.
- **Schema:** `PlanRecord` gains an `error` column in the existing `create_all`
  metadata — consistent with the project's "`rm revalid.db` on schema change"
  development practice (no formal migrations yet).
- **Status `proposed`:** the new lifecycle states and the "failed generation is a
  persisted version, not a `422`" behaviour change are Álvaro's to ratify.
