"""Browser-driven verification probes via Playwright (FR-14, ADR-0018).

Some findings are only verifiable in a real browser — a client-side/DOM XSS
executes JavaScript the HTTP response never reveals. This module runs such a
probe under the *same* constraints as an HTTP probe: it goes through the FR-05
approval gate and the FR-08 sanity guard unchanged (the executor is swapped, the
guard is not), and every request the browser makes is held to the FR-06 allowlist
— checked before navigation and on every in-page request.

The browser's observation (did the injected payload execute? was it reflected?)
is serialized into the probe's :class:`~revalid.domain.Evidence`, so the verdict
stays a pure function of stored evidence and re-derives offline (FR-10): see
:func:`decode_observation` and ``revalid.retest.assess_browser_xss``.

Playwright is an **optional** dependency (the ``browser`` extra). It is imported
lazily so the package — and every HTTP-only path — works without it; using a
browser probe without it installed raises :class:`BrowserProbeUnavailableError`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from revalid.allowlist import TargetGuard
from revalid.domain import Evidence, Probe

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Browser, Dialog

# Browser probe kinds share this prefix so the executor can route them to a
# browser runner instead of the HTTP client (see revalid.retest.is_browser_probe).
BROWSER_KIND_PREFIX = "browser-"
BROWSER_XSS_KIND = "browser-xss"

# A unique token inside the injected payload: an alert carrying it, or the payload
# surviving in the DOM, is unambiguously *our* probe and not incidental content.
_XSS_MARKER = "revalid-xss-probe"
_XSS_PAYLOAD = f"<iframe src=\"javascript:alert('{_XSS_MARKER}')\">"

# A browser runner: produce evidence for one browser probe. The guard is bound
# behind it (see make_browser_runner), mirroring the HTTP client dependency.
BrowserRunner = Callable[[Probe], Evidence]


class BrowserProbeUnavailableError(Exception):
    """Raised when a browser probe runs but Playwright (the ``browser`` extra) is absent."""

    def __init__(self) -> None:
        """Explain how to enable browser probes."""
        super().__init__(
            "browser probes require the optional Playwright extra: "
            "`uv sync --extra browser && uv run playwright install chromium`"
        )


def is_browser_probe(probe: Probe) -> bool:
    """Return whether ``probe`` must run in a browser rather than over HTTP (FR-14)."""
    return probe.kind.startswith(BROWSER_KIND_PREFIX)


def stored_xss_probe(base_url: str) -> Probe:
    """Build the browser XSS probe against Juice Shop's client-side search sink (FR-14).

    A DOM-based XSS: the search query is rendered unsanitized, so an ``<iframe>``
    with a ``javascript:`` URL executes — observable only in a browser. The
    ``javascript:`` payload is not a network request, so it never touches the
    allowlist; the page and its assets do, and they stay on ``base_url``.
    """
    return Probe(
        kind=BROWSER_XSS_KIND,
        method="GET",
        url=f"{base_url.rstrip('/')}/#/search?q={_XSS_PAYLOAD}",
        expected_indicator=(
            "A JavaScript dialog carrying the probe marker means the DOM XSS still "
            "executes (still-open); a sanitized page with no execution means fixed."
        ),
    )


@dataclass(frozen=True)
class _Observation:
    """What the browser saw: whether the payload executed and/or survived in the DOM."""

    executed: bool
    dialog_message: str
    payload_reflected: bool
    final_url: str
    status: int


def encode_observation(observation: _Observation) -> str:
    """Serialize a browser observation into an evidence body excerpt (audit trail)."""
    return json.dumps(
        {
            "xss_executed": observation.executed,
            "dialog_message": observation.dialog_message,
            "payload_reflected": observation.payload_reflected,
            "final_url": observation.final_url,
        }
    )


def decode_observation(excerpt: str) -> dict[str, Any] | None:
    """Parse a browser observation from stored evidence, or ``None`` if malformed.

    Pure and offline (a ``json.loads``), so the browser-XSS assessor — and audit
    re-derivation (FR-10) — reproduce the verdict from stored evidence alone.
    """
    try:
        data = json.loads(excerpt)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) and "xss_executed" in data else None


def _guard_route(guard: TargetGuard) -> Callable[[Any], None]:
    """Build a Playwright route handler that aborts off-allowlist network requests.

    Only ``http(s)`` requests are gated (they are the SSRF surface); ``data:`` /
    ``blob:`` / ``about:`` resources are not network egress and pass through.
    """

    def handle(route: Any) -> None:
        url = route.request.url
        if url.startswith(("http://", "https://")) and not guard.is_allowed(url):
            route.abort()
        else:
            route.continue_()

    return handle


def _drive(  # pragma: no cover - drives a live browser; covered by the system test
    browser: Browser, probe: Probe, guard: TargetGuard, timeout_ms: int
) -> _Observation:
    """Navigate to the probe URL and observe whether the injected payload executes."""
    executed = False
    dialog_message = ""

    def on_dialog(dialog: Dialog) -> None:
        nonlocal executed, dialog_message
        executed, dialog_message = True, dialog.message
        dialog.dismiss()

    page = browser.new_page()
    page.route("**/*", _guard_route(guard))
    page.on("dialog", on_dialog)
    response = page.goto(probe.url, wait_until="load", timeout=timeout_ms)
    page.wait_for_timeout(500)  # let the DOM-rendered payload run
    reflected = _XSS_MARKER in page.content()
    return _Observation(
        executed=executed,
        dialog_message=dialog_message,
        payload_reflected=reflected,
        final_url=page.url,
        status=response.status if response is not None else 200,
    )


def _evidence(probe: Probe, observation: _Observation, started: float) -> Evidence:
    """Build evidence carrying the browser observation (still-open/fixed derivable from it)."""
    return Evidence(
        request_method=probe.method,
        request_url=probe.url,
        response_status=observation.status,
        response_body_excerpt=encode_observation(observation),
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


def _unreachable_evidence(probe: Probe, exc: Exception, started: float) -> Evidence:
    """Status-0 evidence for a browser probe whose navigation failed (assessed unreachable)."""
    return Evidence(
        request_method=probe.method,
        request_url=probe.url,
        response_status=0,
        response_body_excerpt=f"browser probe failed: {exc}",
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


def run_browser_probe(  # pragma: no cover - launches a real browser; covered by the system test
    probe: Probe, guard: TargetGuard, *, headless: bool = True, timeout_ms: int = 15_000
) -> Evidence:
    """Execute a browser probe and capture its evidence (FR-14).

    The target is allowlist-checked before the browser launches (a denial is
    surfaced, never swallowed — as with HTTP probes), and every in-page request is
    gated too. A navigation failure yields status-0 evidence so the assessor calls
    it *inconclusive* rather than raising, keeping every retest evidence-backed.

    Raises:
        revalid.allowlist.TargetNotAllowedError: If the probe URL is off-allowlist.
        BrowserProbeUnavailableError: If the Playwright extra is not installed.
    """
    guard.check(probe.url)
    started = time.perf_counter()
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise BrowserProbeUnavailableError() from exc

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            try:
                observation = _drive(browser, probe, guard, timeout_ms)
            finally:
                browser.close()
    except PlaywrightError as exc:
        return _unreachable_evidence(probe, exc, started)
    return _evidence(probe, observation, started)


def make_browser_runner(guard: TargetGuard) -> BrowserRunner:
    """Return a browser runner bound to ``guard`` (the injectable FR-14 executor)."""
    return lambda probe: run_browser_probe(probe, guard)
