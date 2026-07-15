# 0020. Manual report entry: human ingestion bypassing the LLM

Date: 2026-07-15
Status: proposed

## Context

The automatic ingestion path (FR-01 PDF segmentation → FR-03 LLM extraction) does
not generalize to every report on every backend:

- **FR-01 segmentation is format-bound.** `segment_findings` splits on a fixed set
  of heading conventions ("Finding N", "F-01"). A real report whose findings are
  numbered subsections (e.g. `5.1`, `5.2` under "Finding Details") matches none of
  them, so the segmenter falls back to **one document-sized candidate** (observed:
  a 61-page report → a single 94k-char blob).
- **A single mega-candidate is unusable on a small local model.** Fed to a 27B
  Ollama backend it exceeds the context window (truncated to 4096 tokens) and takes
  impractically long, producing no findings.

Álvaro's direction (2026-07-15): **do not add more deterministic segmentation** —
the model should ideally consume the whole document (a future large-context,
capable-backend change). But when that is infeasible — a large report on a small
local model — **a human must be able to enter findings directly**, so the rest of
the system (FR-04 plan → FR-05 approve → FR-07 retest → FR-09 verdict) still works.
The human is the fallback ingester; the tool must not depend on extraction quality.

The pieces to build on already exist: `map_defectdojo_export` (FR-02) maps a
`{findings: [...]}` array to domain `Finding`s (severity aliases, endpoints, step
splitting); `ReportRecord`/`FindingRecord` already model a report with attached
findings; and the SPA already drives the full flow from a report's findings.

## Decision

Add a **manual ingestion path that bypasses the LLM entirely**, exposed in the UI.

- **`POST /api/reports/manual`** accepts `{"label": str, "findings": [...]}`,
  reuses `map_defectdojo_export` per finding (so manual and structured-import
  ingestion share one mapping — no duplication), and creates a **`ready`** report
  with its findings **attached** (`report_id` set) and `model = "manual"`. Downstream
  it is identical to an extracted report, so FR-04→FR-09 work unchanged. Empty/invalid
  input fails closed with `422`.
- **SPA "Create report manually" view (`/new`)** with two modes sharing one payload:
  a **structured form** (report label + repeatable finding rows: title, severity,
  description, endpoints, steps) and a **JSON mode** (paste or upload a `.json`
  file). Reached from a "Create a report manually" action on the overview.
- **Attach to a report, not orphaned findings.** Unlike `/findings/import` (which
  creates report-less findings the SPA cannot navigate to), the manual path creates
  a report so the findings are first-class in the existing UI.

## Alternatives considered

- **Broaden the FR-01 deterministic segmenter** to recognize more heading
  conventions (numbered subsections, severity headers…). Rejected (Álvaro): brittle
  whack-a-mole across report formats, and the wrong direction — he wants the model to
  consume whole documents, not more regex. Manual entry is the escape hatch for when
  the model cannot.
- **LLM-driven whole-document extraction** (chunk the full text, large context
  window). This is the preferred *automatic* direction, but it depends on a capable,
  large-context backend and is a separate future change (ADR to come). Manual entry is
  orthogonal and works on **any** backend today.
- **Reuse `/findings/import`** (report-less findings). Rejected: the SPA surfaces
  findings only under a report, so imported report-less findings are not navigable.
- **A bespoke manual-finding schema** distinct from the DefectDojo mapping. Rejected:
  duplicates severity/endpoint/step normalization; the FR-02 mapper already does it.

## Consequences

- **Easier:** the tool works on any backend and any report format — a human can
  always get findings in, so the retest pipeline is usable independent of extraction
  quality (and small local models become viable end-to-end). Manual and structured
  ingestion share one mapping.
- **Harder / accepted:** manual entry trusts the human's input beyond the DefectDojo
  mapping's basic validation — consistent with the single-user threat model
  (ADR-0008), where the human is trusted. Lineage is preserved: the mapper echoes the
  submitted entry into `Finding.raw` (NFR-02).
- **Scope:** this adds a capability beyond FR-01/FR-03. Whether it becomes a numbered
  SRS requirement or a documented enhancement is Álvaro's call. It is the first piece
  of the broader human-in-the-loop control surface; the LLM retest summary, human
  verdict adjudication, user-configurable model/provider, and user-validated scope are
  separate upcoming ADRs.
- **Status `proposed`:** the bypass-the-LLM path and its DefectDojo-schema reuse are
  Álvaro's to ratify.
