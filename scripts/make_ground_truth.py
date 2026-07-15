"""FR-15 authoring aid: emit a ground-truth skeleton from a run export.

Usage::

    uv run python scripts/make_ground_truth.py --export run-export.json \
        --out tests/data/eval/ground_truth.json

Reads an FR-12 run export and writes a ground-truth skeleton with one entry per
finding — each ``finding`` title already keyed to the run, ``expected`` left as
the ``TODO`` sentinel — so authoring the evaluation ground truth is fill-in-the-
blanks and the titles are guaranteed to match. Writes to ``--out`` or stdout.

Then replace every ``"expected": "TODO"`` with ``still_open`` / ``fixed`` /
``inconclusive``, set ``ambiguous: true`` where *inconclusive* is the only
defensible verdict, and score with ``make eval EXPORT=… GROUND_TRUTH=…``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from revalid.eval import ground_truth_skeleton, load_export


def main(argv: list[str] | None = None) -> int:
    """Emit a ground-truth skeleton for the findings in a run export."""
    parser = argparse.ArgumentParser(description="FR-15 ground-truth skeleton generator")
    parser.add_argument("--export", required=True, type=Path, help="FR-12 run export JSON")
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)

    skeleton, duplicates = ground_truth_skeleton(load_export(args.export))
    text = json.dumps(skeleton, indent=2) + "\n"
    count = len(skeleton["findings"])

    if args.out is not None:
        args.out.write_text(text)
        print(f"wrote {args.out} — {count} finding(s) to fill in", file=sys.stderr)
    else:
        print(text)

    if duplicates:
        print(
            f"! {len(duplicates)} finding(s) share a title after normalization and would "
            f"collide in scoring — disambiguate: {duplicates}",
            file=sys.stderr,
        )
    print(
        'next: replace every "expected": "TODO" with still_open|fixed|inconclusive; '
        "set ambiguous=true where inconclusive is the only defensible verdict.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
