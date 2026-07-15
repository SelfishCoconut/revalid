"""System test: verify Juice Shop's DOM XSS in a real browser (FR-14 acceptance).

Requires the lab (`make lab-up`) AND the Playwright extra
(`uv sync --extra browser && uv run playwright install chromium`). Skips
gracefully when either is missing, so a developer without the full setup still
gets a green suite; CI (system-tests.yml) provisions both. The acceptance: a
finding verifiable only in a browser (the DOM XSS executes client-side, invisible
at HTTP level) receives a correct *still-open* verdict.
"""

import time

import httpx
import pytest

from revalid.allowlist import load_allowlist
from revalid.browser import run_browser_probe, stored_xss_probe
from revalid.domain import VerdictStatus
from revalid.retest import assess_evidence, lab_base_url


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
def test_dom_xss_still_open_in_browser() -> None:
    pytest.importorskip("playwright.sync_api", reason="install the `browser` extra")
    base_url = lab_base_url()
    if not _wait_for_lab(base_url):
        pytest.skip(f"lab not reachable at {base_url}; run `make lab-up`")

    evidence = run_browser_probe(stored_xss_probe(base_url), load_allowlist())
    if evidence.response_status == 0:
        pytest.skip("browser could not run; try `uv run playwright install chromium`")

    verdict = assess_evidence("browser-xss", evidence)
    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "browser_xss_executed"
    assert "xss_executed" in verdict.matched_indicators
