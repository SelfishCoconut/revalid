"""Unit tests for FR-14 browser probes — the pure logic and the execution routing.

No real browser is launched here: Playwright is exercised by the system-marked
test. These cover the probe shape, the observation encode/decode, the browser-XSS
assessor (through the shared ``assess_evidence``), and that the FR-05 chokepoint
routes a browser probe to an injected runner under the same guard.
"""

from __future__ import annotations

import json

import httpx
import pytest

from revalid.approval import approve_plan, execute_approved_plan, save_generated_plan
from revalid.browser import (
    BROWSER_XSS_KIND,
    BrowserProbeUnavailableError,
    decode_observation,
    is_browser_probe,
    make_browser_runner,
    stored_xss_probe,
)
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import Evidence, Finding, Probe, RetestPlan, Severity, VerdictStatus
from revalid.findings import create_finding
from revalid.plan import PlanResult
from revalid.retest import assess_evidence

_BASE = "http://localhost:3000"


def _observation_evidence(*, executed: bool, reflected: bool) -> Evidence:
    return Evidence(
        request_method="GET",
        request_url=f"{_BASE}/#/search?q=x",
        response_status=200,
        response_body_excerpt=json.dumps(
            {
                "xss_executed": executed,
                "dialog_message": "revalid-xss-probe" if executed else "",
                "payload_reflected": reflected,
                "final_url": f"{_BASE}/",
            }
        ),
    )


def test_is_browser_probe() -> None:
    assert is_browser_probe(stored_xss_probe(_BASE)) is True
    assert is_browser_probe(Probe(kind="sqli-login-bypass", method="POST", url=_BASE)) is False


def test_stored_xss_probe_shape() -> None:
    probe = stored_xss_probe(_BASE + "/")  # trailing slash trimmed
    assert probe.kind == BROWSER_XSS_KIND
    assert probe.method == "GET"
    assert probe.url.startswith(f"{_BASE}/#/search?q=")
    assert "revalid-xss-probe" in probe.url


def test_observation_round_trip_and_malformed() -> None:
    excerpt = _observation_evidence(executed=True, reflected=True).response_body_excerpt
    decoded = decode_observation(excerpt)
    assert decoded is not None and decoded["xss_executed"] is True
    assert decode_observation("not json") is None
    assert decode_observation(json.dumps({"other": 1})) is None  # missing the key


def test_assess_browser_xss_executed_is_still_open() -> None:
    verdict = assess_evidence(
        BROWSER_XSS_KIND, _observation_evidence(executed=True, reflected=True)
    )
    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "browser_xss_executed"


def test_assess_browser_xss_sanitized_is_fixed() -> None:
    verdict = assess_evidence(
        BROWSER_XSS_KIND, _observation_evidence(executed=False, reflected=False)
    )
    assert verdict.status is VerdictStatus.FIXED
    assert verdict.reason_code == "xss_sanitized"


def test_assess_browser_xss_reflected_not_executed_is_inconclusive() -> None:
    verdict = assess_evidence(
        BROWSER_XSS_KIND, _observation_evidence(executed=False, reflected=True)
    )
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "xss_reflected_not_executed"


def test_assess_browser_xss_malformed_is_inconclusive() -> None:
    evidence = Evidence(
        request_method="GET", request_url=_BASE, response_status=200, response_body_excerpt="oops"
    )
    verdict = assess_evidence(BROWSER_XSS_KIND, evidence)
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "browser_probe_malformed"


def test_assess_browser_xss_unreachable_is_inconclusive() -> None:
    # Status 0 (navigation failed) is handled by assess_evidence before the assessor.
    evidence = Evidence(
        request_method="GET", request_url=_BASE, response_status=0, response_body_excerpt="failed"
    )
    verdict = assess_evidence(BROWSER_XSS_KIND, evidence)
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "target_unreachable"


def test_make_browser_runner_is_callable() -> None:
    from revalid.allowlist import load_allowlist

    assert callable(make_browser_runner(load_allowlist()))


def _seed_browser_plan(session_maker: object) -> None:
    session = session_maker()  # type: ignore[operator]
    create_finding(session, Finding(title="DOM XSS", severity=Severity.HIGH))
    session.commit()
    probe = stored_xss_probe(_BASE)
    plan = RetestPlan(finding_title="DOM XSS", actions=(probe,), raw={"finding_title": "DOM XSS"})
    save_generated_plan(session, 1, PlanResult(plan=plan))
    approve_plan(session, 1)
    session.close()


def test_execute_routes_browser_probe_to_the_runner() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    _seed_browser_plan(sessions)
    fake = lambda _probe: _observation_evidence(executed=True, reflected=True)  # noqa: E731

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as client:
        [record] = execute_approved_plan(sessions(), client, 1, browser_runner=fake)

    assert record.probe_kind == BROWSER_XSS_KIND
    assert record.status == VerdictStatus.STILL_OPEN.value  # ran via the browser runner, not HTTP


def test_execute_browser_probe_without_runner_raises() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    _seed_browser_plan(sessions)
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(BrowserProbeUnavailableError):
            execute_approved_plan(sessions(), client, 1, browser_runner=None)


# --- pure browser helpers (no live browser) -------------------------------


class _FakeRoute:
    """Stand-in for a Playwright Route recording whether it continued or aborted."""

    def __init__(self, url: str) -> None:
        self.request = type("Req", (), {"url": url})()
        self.action: str | None = None

    def abort(self) -> None:
        self.action = "abort"

    def continue_(self) -> None:
        self.action = "continue"


def test_guard_route_aborts_off_allowlist_http_only() -> None:
    from revalid.allowlist import TargetGuard
    from revalid.browser import _guard_route

    handler = _guard_route(TargetGuard(frozenset({"http://localhost:3000/*"})))

    allowed = _FakeRoute("http://localhost:3000/main.js")
    blocked = _FakeRoute("http://evil.example/x")
    data_uri = _FakeRoute("data:text/html,<b>x</b>")
    for route in (allowed, blocked, data_uri):
        handler(route)
    assert (allowed.action, blocked.action, data_uri.action) == ("continue", "abort", "continue")


def test_evidence_carries_a_decodable_observation() -> None:
    import time

    from revalid.browser import _evidence, _Observation

    obs = _Observation(
        executed=True, dialog_message="m", payload_reflected=True, final_url=_BASE, status=200
    )
    evidence = _evidence(stored_xss_probe(_BASE), obs, time.perf_counter())
    assert evidence.response_status == 200
    decoded = decode_observation(evidence.response_body_excerpt)
    assert decoded is not None and decoded["xss_executed"] is True


def test_unreachable_evidence_is_status_zero() -> None:
    import time

    from revalid.browser import _unreachable_evidence

    evidence = _unreachable_evidence(
        stored_xss_probe(_BASE), RuntimeError("boom"), time.perf_counter()
    )
    assert evidence.response_status == 0
    assert "boom" in evidence.response_body_excerpt


def test_browser_unavailable_error_message_names_the_extra() -> None:
    assert "browser" in str(BrowserProbeUnavailableError())
