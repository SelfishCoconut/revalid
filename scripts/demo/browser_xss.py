"""Demo for FR-14: run a browser XSS probe through the approval gate, offline.

Usage::

    uv run python scripts/demo/browser_xss.py

Runs fully offline with a *canned* browser runner (no real browser) to show the
FR-14 wiring: an approved browser-XSS plan executes through the same FR-05
chokepoint and FR-08 guard as an HTTP probe, and the browser observation is
assessed to a verdict. A real run against the lab needs the Playwright extra and
`make lab-up` — see `tests/system/test_browser_xss_system.py`.
"""

from __future__ import annotations

import json

import httpx

from revalid.approval import approve_plan, execute_approved_plan, save_generated_plan
from revalid.browser import BROWSER_XSS_KIND, stored_xss_probe
from revalid.db import IN_MEMORY, FindingRecord, create_db_engine, session_factory
from revalid.domain import Evidence, Finding, Probe, RetestPlan, Severity
from revalid.plan import PlanResult
from revalid.retest import assess_evidence

_BASE = "http://localhost:3000"


def _canned_runner(executed: bool) -> object:
    """A browser runner that returns a fixed observation (stands in for Playwright)."""

    def run(probe: Probe) -> Evidence:
        return Evidence(
            request_method="GET",
            request_url=probe.url,
            response_status=200,
            response_body_excerpt=json.dumps(
                {
                    "xss_executed": executed,
                    "payload_reflected": executed,
                    "dialog_message": "revalid-xss-probe" if executed else "",
                    "final_url": f"{_BASE}/",
                }
            ),
        )

    return run


def main() -> int:
    """Assess a canned observation, then run the full gated browser-probe pipeline."""
    probe = stored_xss_probe(_BASE)
    print(f"1. browser probe: {probe.kind}  GET {probe.url[:60]}...")

    executed = assess_evidence(BROWSER_XSS_KIND, _canned_runner(True)(probe))
    sanitized = assess_evidence(BROWSER_XSS_KIND, _canned_runner(False)(probe))
    print(f"2. payload executed  -> {executed.status.value} ({executed.reason_code})")
    print(f"   payload sanitized -> {sanitized.status.value} ({sanitized.reason_code})")

    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(FindingRecord.from_domain(Finding(title="DOM XSS", severity=Severity.HIGH)))
    session.commit()
    plan = RetestPlan(finding_title="DOM XSS", actions=(probe,), raw={"finding_title": "DOM XSS"})
    save_generated_plan(session, 1, PlanResult(plan=plan))
    approve_plan(session, 1)

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as client:
        [record] = execute_approved_plan(session, client, 1, browser_runner=_canned_runner(True))
    print(
        f"3. through the FR-05 chokepoint + FR-08 guard (browser runner, not HTTP): "
        f"{record.status} ({record.reason_code})"
    )
    print("   (real browser run: `uv sync --extra browser`, `make lab-up`, then the system test)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
