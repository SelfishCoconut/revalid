# 0016. Versioned run export as a Pydantic-generated JSON document with a published schema

Date: 2026-07-15
Status: proposed

## Context

FR-12 (Must) requires the system to export a complete run — reports, findings,
plans, verdicts, evidence, and metrics — as a **versioned JSON document**, and
its acceptance criterion is that the export **validates against a published JSON
schema** that the evaluation harness (FR-15) then consumes. FR-15 is the next
milestone (M5): it grades verdicts against Álvaro-owned ground truth and prints
the results-chapter metrics table, reading its input from this export.

Everything the export needs already lives in the database as typed rows —
`ReportRecord`, `FindingRecord`, `PlanRecord`, `VerdictRecord` — each carrying
its own timestamps and actors after FR-10 (ADR-0015). A run has no single "run
id" row; a run *is* the full database state. Two things are open: the shape of
the exported document (and how its schema stays honest), and where the
version/metrics live.

Two temptations to avoid: hand-writing a JSON Schema (guaranteed to drift from
the code that produces the document), and computing correctness metrics in the
export (the tool has no ground truth — grading is FR-15's job, not the
exporter's).

## Decision

Add `src/revalid/export.py`: a set of Pydantic models mirroring the persisted
entities, assembled by a pure read.

- **`RunExport` is the document.** It nests `ReportExport` / `FindingExport` /
  `PlanExport` / `VerdictExport` (each embedding the existing domain
  `Finding` / `Probe` / `Verdict` models, so the export reuses the domain schema
  rather than re-declaring it) plus a `RunMetrics` block and a `generator`
  provenance stamp (tool + version, NFR-02).
- **Versioned by `SCHEMA_VERSION` (`"1.0"`)**, a constant independent of the
  tool's release version and carried in every document. Bumping it on a breaking
  shape change is how a consumer tells which contract a file follows.
- **The JSON Schema is generated, never hand-written.** `export_schema()` returns
  `RunExport.model_json_schema()`; `make export-schema` publishes it to
  `docs/reference/schemas/run-export.schema.json`, and a unit test fails if the
  committed file drifts from the model — so the published schema can never lie
  about the document.
- **`build_export(session, *, generated_at=None)` is a pure read** in id order
  (deterministic output), `generated_at` injectable so runs/tests reproduce.
  Metrics are **descriptive only** — counts and evidence timing, with
  `verdicts_by_status` always carrying every status key — never correctness
  scores; grading stays in FR-15.
- **Exposed read-only:** `GET /api/export` returns the document, and
  `GET /api/export/schema` returns the schema it validates against.
  `make demo-export` builds a run offline, exports it, and validates the document
  against the published schema with `jsonschema` (a dev-only dependency — the
  export itself needs no validator).

## Alternatives considered

- **Hand-authored JSON Schema.** Rejected: it would drift from the models the
  moment either changed. Generating it from `RunExport` and drift-testing the
  published copy keeps the contract truthful by construction.
- **A dedicated append-only `audit_log` / `run` table to export from.** Rejected
  (as already flagged in ADR-0015): the per-entity rows already carry timestamps
  and actors and fully reconstruct a run, so a separate log would be redundant
  storage, not new information.
- **Flat, denormalized records** (verdict rows carrying copies of their finding
  and plan fields). Rejected: nesting the domain models keeps one source of truth
  for each entity's shape and lets the schema reference them via `$defs`; FR-15
  joins on the ids, which are all present.
- **Compute correctness metrics in the export.** Rejected: the tool holds no
  ground truth, so any "correct/wrong" count here would be meaningless. The
  export ships neutral facts; FR-15 grades them against Álvaro's expected
  verdicts.

## Consequences

- **Easier:** FR-15 has a stable, versioned, self-validating input contract; the
  schema cannot silently diverge from the document (generated + drift-tested);
  the export is a deterministic offline read reusing the domain models.
- **Harder / accepted debt:** a new dev dependency (`jsonschema` +
  `types-jsonschema`) purely for test/demo validation. The export is a full-DB
  snapshot with no filtering (no per-run scoping yet) — acceptable for a
  single-user local tool (ADR-0008) where the database *is* the run; per-run
  scoping can come with FR-15 if the evaluation needs several runs side by side.
- NFR-02's per-LLM-call prompt/parameter capture remains the follow-up noted in
  ADR-0015; the export carries the model name already persisted, not full prompts.
