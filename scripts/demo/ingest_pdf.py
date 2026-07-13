"""Demo for FR-01: extract a PDF pentest report into raw finding candidates.

Usage::

    uv run python scripts/demo/ingest_pdf.py [report.pdf]

Defaults to the synthetic Juice Shop fixture in ``tests/data/`` so
``make demo-ingest-pdf`` is always safe to run. Point it at a malformed file to
see FR-01 reject it with a clear error instead of crashing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from revalid.pdf import PdfError, read_pdf, segment_findings

DEFAULT_REPORT = Path(__file__).parents[2] / "tests" / "data" / "juice_shop_report_synthetic.pdf"


def main() -> int:
    """Run the demo: extract, segment, print candidates (or reject cleanly)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    print(f"Reading {args.report}\n")
    try:
        report = read_pdf(args.report.read_bytes())
    except PdfError as exc:
        print(f"Rejected the document: {exc}", file=sys.stderr)
        return 1

    candidates = segment_findings(report)
    print(f"{report.page_count} page(s), {len(candidates)} finding candidate(s):\n")
    for index, candidate in enumerate(candidates, start=1):
        heading = candidate.heading or "(whole document — no headings detected)"
        excerpt = " ".join(candidate.text.split())[:160]
        print(f"[{index}] {heading}")
        print(f"      {excerpt}...\n")
    print("These candidates are the raw input FR-03's LLM will structure into findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
