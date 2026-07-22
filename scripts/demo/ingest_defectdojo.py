"""Demo for FR-02: ingest a DefectDojo-style JSON export and print the findings.

Usage::

    uv run python scripts/demo/ingest_defectdojo.py [export.json] [--db PATH]

Defaults to the synthetic sample in ``tests/data/`` and a throwaway
database file, so ``make demo-ingest`` is always safe to run.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from sqlalchemy import select

from revalid.db import FindingRecord, create_db_engine, session_factory
from revalid.findings import create_finding, current_version
from revalid.ingest import IngestError, load_defectdojo_export

DEFAULT_EXPORT = Path(__file__).parents[2] / "tests" / "data" / "defectdojo_sample.json"


def main() -> int:
    """Run the demo: parse, persist, read back, print."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", nargs="?", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--db", type=Path, default=None, help="SQLite file (default: temp)")
    args = parser.parse_args()

    db_path = args.db or Path(tempfile.mkdtemp(prefix="revalid-demo-")) / "demo.db"
    print(f"Ingesting {args.export} into {db_path}\n")
    try:
        findings = load_defectdojo_export(args.export.read_text())
    except IngestError as exc:
        print(f"Ingestion rejected the document: {exc}", file=sys.stderr)
        return 1

    engine = create_db_engine(str(db_path))
    factory = session_factory(engine)
    with factory() as session:
        # A finding is a stable identity row plus an append-only version history
        # (FR-16, ADR-0024); ingestion lands version 1 via the findings service.
        for imported in findings:
            create_finding(session, imported)
        session.commit()

    # Read back from the database — what you see below survived persistence.
    with factory() as session:
        for record in session.scalars(select(FindingRecord).order_by(FindingRecord.id)):
            version = current_version(session, record.id)
            if version is None:  # pragma: no cover - a finding always has version 1
                continue
            finding = version.to_domain()
            unmapped = sorted(
                set(finding.raw)
                - {"title", "severity", "description", "steps_to_reproduce", "endpoints"}
            )
            print(f"[{record.id}] {finding.severity.value.upper():8} {finding.title}")
            for endpoint in finding.affected_endpoints:
                print(f"      endpoint: {endpoint}")
            for step in finding.reproduction_steps:
                print(f"      step: {step}")
            print(f"      unmapped fields kept in raw: {unmapped}\n")
    print(f"{len(findings)} findings imported and read back from {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
