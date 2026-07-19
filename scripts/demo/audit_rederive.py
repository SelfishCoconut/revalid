"""Demo for FR-10: re-derive every verdict from the stored audit trail.

Usage::

    uv run python scripts/demo/audit_rederive.py

Runs fully offline: store an agentic retest verdict (a session transcript plus the
recorded conclusion), then re-derive every verdict from its persisted transcript
alone — nothing is re-executed — and print the reproduction result (FR-10
acceptance / NFR-02).
"""

from __future__ import annotations

from revalid import retest_session as rs
from revalid.audit import rederive_run
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import Finding, SessionEventKind, Severity, VerdictStatus
from revalid.findings import create_finding


def main() -> int:
    """Store an agentic verdict, then re-derive it from the stored transcript."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    create_finding(session, Finding(title="SQLi login", severity=Severity.CRITICAL))
    session.commit()

    sid = rs.create_session(session, finding_id=1, model="demo").id
    rs.append_event(
        session,
        sid,
        SessionEventKind.COMMAND_OUTPUT,
        {
            "command": "curl -s http://localhost:3000/rest/user/login",
            "stdout": '{"authentication": {"token": "t"}}',
            "stderr": "",
            "exit_code": 0,
            "elapsed_ms": 12,
        },
    )
    rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
    print(
        f"1. stored verdict from an agentic retest: {VerdictStatus.STILL_OPEN} (agentic_conclusion)"
    )

    report = rederive_run(session)
    print(
        f"2. re-derived {report.reproduced}/{report.total} verdict(s) from the stored transcript "
        f"alone -- nothing re-executed; discrepancies: {len(report.discrepancies)}"
    )
    print(f"3. audit fully reproducible (FR-10 AC): {report.ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
