"""Demo for FR-10: re-derive every verdict from the stored audit trail.

Usage::

    uv run python scripts/demo/audit_rederive.py

Runs fully offline: store a verdict by executing an approved plan against a mock
target, then re-derive every verdict from its persisted evidence alone — no probe
is re-run — and print the reproduction result (FR-10 acceptance / NFR-02).
"""

from __future__ import annotations

import httpx

from revalid.approval import approve_plan, execute_approved_plan, save_generated_plan
from revalid.audit import rederive_run
from revalid.db import IN_MEMORY, FindingRecord, create_db_engine, session_factory
from revalid.domain import Finding, Probe, RetestPlan, Severity
from revalid.plan import PlanResult


def _plan() -> PlanResult:
    probe = Probe(
        kind="sqli-login-bypass",
        method="POST",
        url="http://localhost:3000/rest/user/login",
        json_body={"email": "' OR 1=1--", "password": "x"},
    )
    plan = RetestPlan(
        finding_title="SQLi login", actions=(probe,), raw={"finding_title": "SQLi login"}
    )
    return PlanResult(plan=plan)


def main() -> int:
    """Store a verdict from a live retest, then re-derive it from stored data."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(FindingRecord.from_domain(Finding(title="SQLi login", severity=Severity.CRITICAL)))
    session.commit()
    save_generated_plan(session, 1, _plan())
    approve_plan(session, 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        [verdict] = execute_approved_plan(session, client, 1)
    print(f"1. stored verdict from a live retest: {verdict.status} ({verdict.reason_code})")

    report = rederive_run(session)
    print(
        f"2. re-derived {report.reproduced}/{report.total} verdict(s) from stored evidence "
        f"alone -- no probe re-executed; discrepancies: {len(report.discrepancies)}"
    )
    print(f"3. audit fully reproducible (FR-10 AC): {report.ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
