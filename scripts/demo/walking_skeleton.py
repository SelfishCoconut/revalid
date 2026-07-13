"""Walking-skeleton demo (FR-07/FR-09): ingest -> probe -> verdict.

Usage::

    make lab-up                                        # start the Juice Shop lab
    uv run python scripts/demo/walking_skeleton.py     # or: make demo-walking-skeleton

Ingests the synthetic login-SQLi finding, retests it against the local lab
through the FR-06 allowlist, prints the evidence-backed verdict, and persists
it. Exits non-zero if the lab is unreachable so the target fails loudly.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sqlalchemy import select

from revalid.allowlist import load_allowlist
from revalid.db import FindingRecord, VerdictRecord, create_db_engine, session_factory
from revalid.ingest import load_defectdojo_export
from revalid.retest import build_probe_client, lab_base_url, login_sqli_probe, run_probe

DATA = Path(__file__).parents[2] / "tests" / "data" / "juice_shop_login_sqli.json"


def main() -> int:
    """Run the walking-skeleton flow and print each stage."""
    db_path = Path(tempfile.mkdtemp(prefix="revalid-demo-")) / "demo.db"
    factory = session_factory(create_db_engine(str(db_path)))

    findings = load_defectdojo_export(DATA.read_text())
    with factory() as session:
        session.add_all(FindingRecord.from_domain(f) for f in findings)
        session.commit()
        finding = session.scalars(select(FindingRecord).order_by(FindingRecord.id)).first()
        if finding is None:
            print("no findings ingested", file=sys.stderr)
            return 1
        finding_id, title = finding.id, finding.title

    print("== INGEST ==")
    print(f"[{finding_id}] {title}\n")

    probe = login_sqli_probe(lab_base_url())
    print("== PROBE ==")
    print(f"{probe.method} {probe.url}")
    print(f"payload: {probe.json_body}\n")

    with build_probe_client(load_allowlist()) as client:
        verdict = run_probe(client, probe)

    with factory() as session:
        session.add(VerdictRecord.from_domain(finding_id, probe.kind, verdict))
        session.commit()

    evidence = verdict.evidence
    print("== EVIDENCE ==")
    print(f"HTTP {evidence.response_status}  ({evidence.elapsed_ms:.0f} ms)")
    print(f"body: {evidence.response_body_excerpt[:160]}\n")

    print("== VERDICT ==")
    print(f"status:     {verdict.status.value}")
    print(f"reason:     {verdict.reason_code}")
    print(f"rationale:  {verdict.rationale}")
    print(f"indicators: {list(verdict.matched_indicators)}")
    print(f"\npersisted to {db_path}")

    if verdict.reason_code == "target_unreachable":
        print("\nLab unreachable — is it up? Run: make lab-up", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
