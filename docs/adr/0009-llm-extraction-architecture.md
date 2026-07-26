# 0009. LLM finding extraction: per-candidate Pydantic AI with a schema-validation gate

Date: 2026-07-13
Status: superseded by [0047](0047-whole-document-llm-ingestion.md)

## Context

FR-03 requires extracting each finding — title, severity, description, impact,
attack vector, affected endpoint(s), and ordered reproduction steps — from
unstructured report text into a validated Pydantic schema, where output that
fails validation is "retried/flagged, never silently accepted". The stack is
fixed (ADR-0002: Pydantic AI, Claude primary) and the input seam is fixed
(ADR-0007: FR-01 emits text and best-effort finding candidates). This ADR fixes
*how* extraction is structured on top of those.

## Decision

We will extract **one model call per FR-01 finding candidate**, with the agent's
validated output typed as **`list[ExtractedFinding]`**, and gate every result
through Pydantic schema validation before it can become a domain finding.

- `build_extraction_agent(model)` builds a Pydantic AI `Agent` with
  `output_type=list[ExtractedFinding]` and a fixed extraction instruction. The
  model is **injectable** (Claude by default; tests pass `TestModel`/
  `FunctionModel`; FR-13 will swap in Ollama) and constructed with
  `defer_model_check=True` so it never needs the network to build.
- `ExtractedFinding` is the **validation gate**: all FR-03 fields are required
  (title non-empty; severity a `Severity` enum). Pydantic AI validates the
  model's tool output against it and retries on mismatch.
- `extract_report(agent, report)` runs one call per candidate and returns an
  `ExtractionReport(findings, failures)`. Valid output is mapped to domain
  `Finding`s; a candidate whose output never validates (retries exhausted →
  `UnexpectedModelBehavior`) is recorded as an `ExtractionFailure` and **not**
  mapped or persisted — this is the FR-03 "invalid never reaches persistence"
  property, enforced structurally rather than by convention.
- The **`list` output type** means a well-segmented candidate yields exactly one
  finding, while the no-heading whole-document candidate (FR-01 fallback) can
  yield several — one code path covers both.
- The domain `Finding` gains `impact` and `attack_vector` (FR-03 mandates them).
  Each extracted finding's `raw` records lineage — model name and source text —
  for the audit trail (FR-10 / NFR-02).

## Alternatives considered

- **One whole-document call returning `list[ExtractedFinding]`.** Rejected:
  coarser gate (one malformed finding forces the entire batch to retry/flag),
  larger context per call, and it leans on the model to segment. Per-candidate
  gives 1:1 error attribution, bounded context, and reuses the ADR-0007 seam —
  and the `list` output still handles the unstructured case.
- **Free-form / hand-parsed JSON instead of structured output.** Rejected:
  reinvents the validation-and-retry loop Pydantic AI already provides; ADR-0002
  fixed the framework.
- **Accept partial findings when some fields are missing.** Rejected: FR-03
  requires invalid output never to reach persistence — flag it instead.

## Consequences

- **Easier:** the schema gate and retry loop come from the framework; extraction
  is fully testable offline with `TestModel`/`FunctionModel`; the injectable
  model is the seam FR-13 needs.
- **Domain/DB change:** `Finding` and `FindingRecord` gain `impact` /
  `attack_vector`. Fresh SQLite databases pick them up via `create_all`; there is
  no migration for pre-existing dev database files (throwaway).
- **Testing:** the integration test uses a deterministic `FunctionModel` that
  exercises the whole pipeline offline (no API key, no network). The FR-03
  ≥90%-well-formed acceptance is measured on Álvaro's real report by the
  evaluation harness (FR-15), not asserted here.
- **No HTTP endpoint yet:** extraction ships as a library + `make demo-extract`.
  The ingest → extract → persist endpoint arrives with the plan/approval flow
  (FR-04/FR-05/FR-11).
- **Status `proposed`:** the per-candidate structure is the choice to ratify.
