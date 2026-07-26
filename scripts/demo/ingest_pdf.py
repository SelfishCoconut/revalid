"""Demo for FR-01: extract a PDF pentest report into LLM-ready Markdown text.

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

from revalid.pdf import PdfError, read_pdf

DEFAULT_REPORT = Path(__file__).parents[2] / "tests" / "data" / "juice_shop_report_synthetic.pdf"


def main() -> int:
    """Run the demo: extract a PDF to whole-document Markdown (or reject cleanly)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    print(f"Reading {args.report}\n")
    try:
        report = read_pdf(args.report.read_bytes())
    except PdfError as exc:
        print(f"Rejected the document: {exc}", file=sys.stderr)
        return 1

    print(f"{report.page_count} page(s), {len(report.text)} characters of Markdown.\n")
    excerpt = report.text[:800]
    print(excerpt + ("\n..." if len(report.text) > len(excerpt) else ""))
    print(
        "\nThis whole-document Markdown is the single input FR-03's LLM structures into findings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
