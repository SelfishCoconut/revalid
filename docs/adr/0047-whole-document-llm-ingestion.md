# 0047. Whole-document LLM ingestion: PyMuPDF4LLM Markdown, one call, no regex segmentation

Date: 2026-07-26
Status: proposed

## Context

FR-01 ingests a PDF report; FR-03 turns it into schema-validated findings. The
original design (ADR-0007, ADR-0009) split the report into candidate sections
with a **regex heading segmenter** (`segment_findings`: "Finding N", "F-01", …)
and ran the extractor **once per candidate**. The seam kept extraction offline-
testable and the per-candidate call bounded the context.

That segmenter is format-bound, and the boundary failed silently on unfamiliar
reports. Measured in the Chapter 5 study: on the nine-finding TryHackMe write-up
its heading conventions matched all but one section (8/9); on a real twelve-finding
assessment report — whose findings are numbered subsections, not "Finding N"
headings — it matched *nothing* and collapsed the whole 61-page document into a
single 94 000-character candidate. The failure was located precisely in the
regular expressions ahead of the model, not the model, and the design response at
the time was a manual-entry fallback (ADR-0020), with a large-context model
consuming the whole document named as the right long-term fix.

This ADR does that fix. Álvaro's directive: extraction must not depend on regex;
the model should ingest the entire report and return the findings.

## Decision

Extract the **whole report in one model call**. Drop heading segmentation
entirely.

- **PDF → Markdown with PyMuPDF4LLM** (legacy mode), replacing pdfplumber.
  `read_pdf(bytes) -> PdfReport` renders the document to GitHub-flavoured
  Markdown, so headings, tables and lists survive as structure the model reads.
  Legacy mode (`pymupdf4llm.use_layout(False)`) is deterministic — pure text→
  Markdown, no ML layout model and no Tesseract OCR — which matters for NFR-02
  reproducibility and keeps image-only PDFs out of scope (rejected, not
  transcribed). FR-01 stays LLM-free and fail-closed: non-PDF, corrupt, and
  text-free inputs still raise a clear `PdfError`.
- **One call, `list[ExtractedFinding]`.** `extract_report_async` sends
  `report.text` to the extraction agent once and maps the returned list to domain
  findings. `segment_findings`, `FindingCandidate` and `_FINDING_HEADING` are
  deleted. Pydantic AI stays the framework (ADR-0002/0010) — it already provides
  schema-validated structured output and the `TestModel`/`FunctionModel` harness,
  so no separate structured-output library (e.g. Instructor) is introduced.
- **The schema gate is preserved.** `ExtractedFinding` still validates every
  field; output that never validates (retries exhausted) is flagged as a single
  `ExtractionFailure` rather than persisted — the FR-03 "invalid never reaches
  persistence" property is unchanged, now all-or-nothing over the one call.
- **Output budget.** The one call emits every finding at once, a far larger
  response than a per-candidate call, so the extraction agent sets an explicit
  `max_tokens` (8192); without it the backend falls back to a tiny provider
  default and truncates before the tool call completes.
- **Context window is the new boundary.** The whole report plus its structured
  output must fit the backend's context. On a hosted reasoning backend (Claude,
  ~200k) this is ample and the report is read at once. A small local model has a
  few-thousand-token window, so it handles a short report (measured: the 4-finding
  synthetic fixture extracts 4/4 and the `one_finding_report.pdf` fixture 1/1 on
  `ollama:qwen3.5:9b`) but not a full-length one. This is an explicit, honest
  backend requirement, replacing a silent, format-dependent regex failure.

## Alternatives considered

- **Keep pdfplumber + regex segmentation (ADR-0007/0009).** Rejected: it is the
  format-brittleness this ADR removes — the failure was measured, not
  hypothetical.
- **Instructor (or hand-parsed JSON) for structured output.** Rejected: Pydantic
  AI already gives schema validation, retries and offline test models; a second
  library would duplicate that and break the existing harness. ADR-0002 fixed the
  framework.
- **PyMuPDF4LLM layout mode (ML layout + OCR).** Rejected: non-deterministic (an
  ML model per page), heavier, and its default-on Tesseract OCR hard-fails without
  a language data directory. Legacy mode is deterministic and dependency-light and
  extracted *more* text on the assessment report in testing.
- **Per-request `num_ctx` to raise the local Ollama context window.** Rejected as
  ineffective: Ollama's OpenAI-compatible `/v1` endpoint ignores both the
  Modelfile `num_ctx` and a per-request `options.num_ctx` (verified: a 31k-token
  prompt is truncated to the ~2k default regardless). A large local context is an
  Ollama **server** setting (`OLLAMA_CONTEXT_LENGTH`), i.e. the operator's
  deployment choice, not something the app can request — so the app doesn't
  pretend to.

## Consequences

- **Supersedes ADR-0009** (per-candidate extraction) and overturns its explicitly
  rejected "one whole-document call" alternative, with the field evidence ADR-0009
  lacked. The schema gate it introduced is kept.
- **Amends ADR-0007**: the library moves pdfplumber → PyMuPDF4LLM and the "text +
  best-effort candidates" output becomes "whole-document Markdown". The text seam
  ("the model never sees raw PDF bytes") and the fail-closed contract survive.
- **License reversal — accepted.** ADR-0007 rejected PyMuPDF for being **AGPL-3.0**.
  That call predates the single-user threat model (ADR-0008). revalid is a local,
  single-operator tool with a public repository; AGPL's network-copyleft is
  satisfied by source already being public, and using an AGPL dependency does not
  relicense the project's own code or the thesis. The functional gain (robust,
  layout-aware Markdown that removes the format brittleness) is worth it under the
  current threat model. `pymupdf`/`pymupdf4llm` replace `pdfplumber` in
  `pyproject.toml`.
- **Cancellation is interrupt-only.** With one call there is no between-candidates
  checkpoint, so a Stop is honoured by cancelling the in-flight call cross-thread
  (the `ExtractionRegistry` machinery, unchanged); a Stop now yields no partial
  findings (issue #205 semantics adjusted, all-or-nothing).
- **Lineage simplified.** A finding's `raw` keeps `source`/`model`/`extracted`;
  the per-candidate `candidate_heading` and slice `source_text` are dropped (there
  is no per-finding slice). A flagged failure keeps the whole `report.text`.
- **Evaluation numbers change.** The old "8/9 recall, segmenter recovered nothing"
  and "8/8 well-formed on 8 candidates" figures describe the deleted design; the
  Chapter 5 extraction section and SRS FR-01/FR-03 acceptance are rewritten to the
  measured local one-/four-finding demonstration plus the stated context-window
  boundary. A new deterministic `one_finding_report.pdf` fixture +
  `scripts/gen_one_finding_pdf.py` give a runnable local-extraction check
  (`tests/system/test_ollama_extraction.py`).
- **Status `proposed`:** the whole-document reversal, and the AGPL license
  acceptance in particular, are Álvaro's to ratify in async review.
