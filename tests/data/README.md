# Test & evaluation data

Two kinds of data live here, with different provenance and commit rules.

## Committed fixtures (synthetic — safe to version)

Used by the unit and integration suites; deterministic; no third-party or
real-world content.

- **`juice_shop_report_synthetic.pdf`** — a synthetic multi-finding Juice Shop
  report, regenerable with `python scripts/gen_fixture_pdf.py`. Drives the
  FR-01 → FR-03 pipeline tests.
- **`defectdojo_sample.json`** — a DefectDojo-style structured export for the
  FR-02 ingest tests.
- **`juice_shop_login_sqli.json`** — the login-SQLi finding used by the FR-07
  walking-skeleton probe against the lab.

## Evaluation reference report (external — kept local, not re-hosted)

The M2 / FR-15 evaluation runs the pipeline against a **real** OWASP Juice Shop
penetration-test report rather than the synthetic fixture:

> **Juice Shop Pentest Report** by **Nozipho Mthimunye** —
> <https://github.com/Nozipho-Mth/Juice-Shop-Pentest-Report>

Thanks to the author for making it public; it's a genuinely useful real-world
input for testing the extraction pipeline. The upstream repository carries **no
license**, so the PDF is **not redistributed here** — it stays local and
git-ignored (`/*.pdf`). To reproduce the evaluation, download it from the link
above and point the tooling at it, e.g.:

```sh
REVALID_LLM_MODEL=ollama:<model> OLLAMA_BASE_URL=http://localhost:11434/v1 \
  uv run python scripts/demo/extract_pdf.py "Juiceshop THM Report.pdf"
```

All targets, IPs, and accounts in that report are OWASP Juice Shop lab
artefacts (e.g. `admin@juice-sh.op`) — no client or engagement data (NFR-04).

## Evaluation ground truth (`eval/`)

The FR-15 harness (`src/revalid/eval.py`, ADR-0017) scores a run against a
**ground-truth** file: one expected verdict per evaluation-set finding, matched
to the run by finding title.

- **`eval/ground_truth.example.json`** — a documented template (synthetic
  placeholder findings). Copy it to author the real ground truth, keying each
  entry's `finding` to the exact title as extracted in a real run export, setting
  `expected` (`still_open` / `fixed` / `inconclusive`) and `ambiguous: true` for
  findings whose only defensible verdict is *inconclusive* (the NFR-01
  hard-constraint cases).

Rather than transcribe titles by hand, **generate a pre-keyed skeleton** from a
real export and just fill in the verdicts:

```sh
make ground-truth-skeleton EXPORT=<run-export.json> OUT=tests/data/eval/ground_truth.json
```

Every finding is emitted with its title already keyed and `expected` set to the
`TODO` sentinel; the generator warns about any titles that collide after
normalization. Because `TODO` is not a valid verdict, an unfilled skeleton won't
load — you can't score a run against placeholders by accident.

Score a run with `make eval EXPORT=<run-export.json> GROUND_TRUTH=<gt.json>`
(produce the export from the app's `GET /api/export`), or see the offline
walkthrough with `make demo-eval`.
