"""System test: retest the login-SQLi finding against the live Juice Shop lab.

Requires the lab (`make lab-up`). Skips gracefully when it is unreachable so a
developer without the lab still gets a green suite; CI (system-tests.yml) starts
the lab and waits for readiness before invoking these tests.
"""

import time

import httpx
import pytest

from revalid.allowlist import load_allowlist
from revalid.domain import VerdictStatus
from revalid.retest import build_probe_client, lab_base_url, login_sqli_probe, run_probe


def _wait_for_lab(base_url: str, timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    version_url = f"{base_url}/rest/admin/application-version"
    while time.monotonic() < deadline:
        try:
            if httpx.get(version_url, timeout=5).status_code == 200:
                return True
        except httpx.RequestError:
            pass
        time.sleep(2)
    return False


@pytest.mark.system
def test_login_sqli_still_open_against_lab() -> None:
    base_url = lab_base_url()
    if not _wait_for_lab(base_url):
        pytest.skip(f"lab not reachable at {base_url}; run `make lab-up`")

    with build_probe_client(load_allowlist()) as client:
        verdict = run_probe(client, login_sqli_probe(base_url))

    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "sqli_auth_bypass_succeeded"
    assert verdict.evidence.response_status == 200
    assert "auth_token_present" in verdict.matched_indicators


@pytest.mark.system
def test_approved_plan_retest_still_open_via_api() -> None:
    """Rewired chokepoint end-to-end: seed+approve a login-SQLi plan, retest via API (FR-05).

    Approval/execution is LLM-free, so the plan is seeded with the real
    ``sqli-login-bypass`` probe (kind that ``assess`` understands) rather than
    generated — a *generated* ``planned-http`` probe would assess as inconclusive.
    The default probe client hits the live lab through the FR-06 transport.
    """
    from fastapi.testclient import TestClient

    from revalid.app import create_app
    from revalid.approval import approve_plan, save_generated_plan
    from revalid.db import IN_MEMORY, create_db_engine, session_factory
    from revalid.domain import RetestPlan
    from revalid.plan import PlanResult

    base_url = lab_base_url()
    if not _wait_for_lab(base_url):
        pytest.skip(f"lab not reachable at {base_url}; run `make lab-up`")

    engine = create_db_engine(IN_MEMORY)
    app = create_app(engine=engine)
    with TestClient(app) as client:
        client.post(
            "/api/findings/import",
            json={"findings": [{"title": "SQLi login", "severity": "Critical"}]},
        )
        with session_factory(engine)() as session:
            plan = RetestPlan(
                finding_title="SQLi login",
                actions=(login_sqli_probe(base_url),),
                raw={"finding_title": "SQLi login"},
            )
            save_generated_plan(session, 1, PlanResult(plan=plan))
            approve_plan(session, 1)
        verdicts = client.post("/api/findings/1/retest").json()

    assert verdicts[0]["status"] == "still_open"
    assert verdicts[0]["reason_code"] == "sqli_auth_bypass_succeeded"
    assert verdicts[0]["plan_version"] == 1
