# 0017. Evaluation harness: score an FR-12 export against title-keyed ground truth with conservative NFR-01 buckets

Date: 2026-07-15
Status: proposed

## Context

FR-15 (Must) requires a harness that runs the evaluation set (the author's Juice
Shop report vs a deliberately vulnerable instance) against ground truth and
produces, from **one command**, the verdict-reliability metrics table (correct /
wrong / inconclusive per finding, totals, timing) for the thesis Results chapter.
NFR-01 (Must) sets the bar the table is read against: **≥70%** of findings get the
correct verdict, with a **hard constraint** that ambiguous cases must end
*inconclusive* — and a confidently wrong verdict *counts double* in the analysis.

The inputs already exist: FR-12 (ADR-0016) emits a complete run as a versioned
JSON document (findings + verdicts + evidence + timing), and the domain fixes the
verdict vocabulary (`still_open` / `fixed` / `inconclusive`). What's undecided is
the *methodology*: how ground truth is expressed and matched, which verdict is
scored when a finding has several, how each verdict is bucketed, and what exactly
counts as an NFR-01 pass. These choices are load-bearing for the thesis, so they
are recorded here rather than left implicit in code.

## Decision

Add `src/revalid/eval.py`, a pure scorer over an FR-12 `RunExport`, plus
`scripts/evaluate.py` (`make eval`) as the one-command entry point.

- **Ground truth** (`GroundTruth`): `target`, `source_report`, and one
  `GroundTruthEntry` per finding — `finding` (title), `expected` verdict, an
  `ambiguous` flag, and a free-text `note`. Authored by Álvaro; a committed
  `tests/data/eval/ground_truth.example.json` documents the shape.
- **Matching by normalized title** (lowercased, whitespace-collapsed). Finding
  row ids are per-run and unstable; the title is the stable natural key, and
  Álvaro authors the ground truth from a real export so the keys align. Findings
  present on only one side are **surfaced** (`unmatched_findings` /
  `unmatched_ground_truth`), never silently dropped — an unmatched entry means the
  ground truth and the run disagree on the finding set, which would otherwise make
  the score quietly wrong.
- **Latest verdict wins.** A finding may accrue several verdicts (re-plans,
  re-runs); the scored one is the highest-id (most recent) verdict, matching what
  the UI shows as current.
- **Four conservative buckets** (`classify`): `correct` (verdict matches
  expected); `inconclusive` (system returned `inconclusive` where the truth was
  definite — a *safe* miss, not a confident error); `wrong` (a *confident* verdict
  contradicting the truth — the dangerous case; on an ambiguous finding it is the
  hard-constraint breach); `no_verdict` (matched but never retested). Crucially, an
  over-cautious `inconclusive` is **not** scored as wrong — this is what makes the
  hard constraint meaningful.
- **NFR-01 pass = `correct_pct ≥ 0.70` AND `wrong_on_ambiguous == 0`.** The report
  also exposes `confidently_wrong` and a `weighted_error` (`= 2 × wrong`) to honor
  the "counts double" analysis rule, and `format_table` prints the table plus a
  PASS/FAIL line; the CLI exits non-zero on FAIL so the evaluation is a single
  gating command.
- **Decoupled from execution.** The scorer reads a stored export and ground truth
  and opens no network — evaluation is reproducible offline and independent of when
  the run happened (it shares the FR-10/FR-12 "stored data is enough" property).

**Scope of this ADR / PR.** This lands the harness, fully unit-tested against
synthetic exports (all four buckets, both NFR-01 failure modes, unmatched
handling) and demonstrated offline (`make demo-eval`). It does **not** fabricate
the real numbers: the actual ground-truth expected-verdict list is Álvaro's design
input, and the reported figure comes from a real run (a live lab retest → FR-12
export → `make eval`). Those complete M5.

## Alternatives considered

- **Match findings by row id.** Rejected: ids are assigned per ingest and differ
  between runs, so a ground-truth file would break on every re-run. Title is
  stable and human-authored.
- **Score `inconclusive` as a miss/failure.** Rejected: it would punish the exact
  caution NFR-01 rewards. Hedging on a definite finding is a *safe* miss; only a
  confident contradiction is `wrong`.
- **Fail NFR-01 on any confident error, ambiguous or not.** Considered; kept the
  pass rule literal to NFR-01 (ratio + ambiguous hard constraint) but still surface
  total `confidently_wrong`/`weighted_error` prominently, since a confident wrong
  `fixed` on a still-open finding is the worst error and the reader must see it.
- **Compute the metrics inside FR-12 export.** Rejected (already in ADR-0016): the
  tool holds no ground truth, so grading lives here, downstream of the neutral
  export.
- **A bespoke report format / notebook.** Rejected as premature: a plain text
  table from one command meets the FR-15 AC and is diffable; richer rendering can
  come if the Results chapter needs it.

## Consequences

- **Easier:** the Results-chapter number is one reproducible command over stored
  data; the conservative buckets make NFR-01's "confidently wrong counts double"
  and "ambiguous ⇒ inconclusive" directly measurable; unmatched surfacing prevents
  a silently-wrong score.
- **Harder / accepted debt:** title matching needs the ground truth authored from a
  real export (a typo shows up as unmatched, not a crash — acceptable and visible).
  The real evaluation figure is pending Álvaro's ground truth + a live-lab run;
  until then the harness is proven on synthetic data only.
- FR-14 (Playwright browser probes, Could) would add DOM-verifiable findings to the
  same export and thus the same scorer with no change here.
