"""FR-15 evaluation harness CLI: score a run export against ground truth.

Usage::

    uv run python scripts/evaluate.py --export run-export.json --ground-truth gt.json

Reads an FR-12 run export and a ground-truth file, prints the verdict-reliability
metrics table (correct / wrong / inconclusive per finding, totals, timing), and
exits non-zero when the run does not meet NFR-01 — so the evaluation is a single
gating command (FR-15 acceptance). No network, no lab: it scores stored data.

Produce the export first with the app's ``GET /api/export`` (or
``make demo-export`` for a synthetic one); author the ground truth from
``tests/data/eval/ground_truth.example.json``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from revalid.eval import evaluate, format_table, load_export, load_ground_truth


def main(argv: list[str] | None = None) -> int:
    """Score a run export against ground truth and print the metrics table."""
    parser = argparse.ArgumentParser(description="FR-15 evaluation harness")
    parser.add_argument("--export", required=True, type=Path, help="FR-12 run export JSON")
    parser.add_argument(
        "--ground-truth", required=True, type=Path, help="ground-truth JSON (expected verdicts)"
    )
    args = parser.parse_args(argv)

    report = evaluate(load_export(args.export), load_ground_truth(args.ground_truth))
    print(format_table(report))
    return 0 if report.nfr01_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
