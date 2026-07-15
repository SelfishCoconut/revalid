"""Retest engine: execute a verification probe and derive a verdict (FR-07/FR-09).

The walking skeleton ships one hardcoded probe — the OWASP Juice Shop SQL
injection *login bypass* — executed over the FR-06 :class:`AllowlistTransport`
so no request can reach an unauthorized target. Executing the probe captures
request/response evidence; :func:`assess` maps that evidence to a
still-open / fixed / inconclusive :class:`Verdict`. More probe kinds join this
module as their finding types arrive (FR-03/FR-04).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

import httpx

from revalid.allowlist import AllowlistTransport, TargetGuard
from revalid.browser import BROWSER_XSS_KIND, decode_observation
from revalid.domain import Evidence, Probe, Verdict, VerdictStatus

_LAB_ENV = "REVALID_LAB_BASE_URL"
DEFAULT_LAB_BASE_URL = "http://localhost:3000"

# Juice Shop's canonical auth-bypass payload: a tautology in the email field
# that collapses the login WHERE-clause. Verification-only — it reads back an
# existing session, it does not modify data.
_SQLI_LOGIN_EMAIL = "' OR 1=1--"
_BODY_EXCERPT_LIMIT = 16_384


def lab_base_url() -> str:
    """Return the retest target base URL (``$REVALID_LAB_BASE_URL`` or default)."""
    return os.environ.get(_LAB_ENV, DEFAULT_LAB_BASE_URL)


def login_sqli_probe(base_url: str) -> Probe:
    """Build the SQL-injection login-bypass probe against ``base_url``."""
    return Probe(
        kind="sqli-login-bypass",
        method="POST",
        url=f"{base_url.rstrip('/')}/rest/user/login",
        headers={"Content-Type": "application/json"},
        json_body={"email": _SQLI_LOGIN_EMAIL, "password": "x"},
        expected_indicator=(
            "HTTP 200 with an authentication token means the login-bypass SQLi "
            "is still open; HTTP 401 means it is fixed."
        ),
    )


def build_probe_client(guard: TargetGuard, *, timeout: float = 10.0) -> httpx.Client:
    """Build an httpx client that enforces ``guard`` and never follows redirects.

    ``follow_redirects=False`` keeps a 3xx as captured evidence instead of
    chasing it around the allowlist guard (allowlist design decision D2).
    """
    transport = AllowlistTransport(httpx.HTTPTransport(), guard)
    return httpx.Client(transport=transport, follow_redirects=False, timeout=timeout)


def execute(client: httpx.Client, probe: Probe) -> Evidence:
    """Send ``probe`` and capture request/response/timing evidence.

    Raises:
        httpx.RequestError: If the target is unreachable.
        revalid.allowlist.TargetNotAllowedError: If the probe URL is not
            allowlisted — surfaced, never swallowed, since it means the probe
            attempted an unauthorized target.
    """
    started = time.perf_counter()
    response = client.request(probe.method, probe.url, headers=probe.headers, json=probe.json_body)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return Evidence(
        request_method=probe.method,
        request_url=probe.url,
        request_body=json.dumps(probe.json_body) if probe.json_body is not None else "",
        response_status=response.status_code,
        response_headers=dict(response.headers),
        response_body_excerpt=response.text[:_BODY_EXCERPT_LIMIT],
        elapsed_ms=elapsed_ms,
    )


def run_probe(client: httpx.Client, probe: Probe) -> Verdict:
    """Execute ``probe`` and return the assessed verdict, evidence attached.

    An unreachable target yields an inconclusive verdict rather than raising, so
    every retest still produces an evidence-backed verdict (FR-09). Assessment is
    delegated to :func:`assess_evidence` so the live path and audit re-derivation
    (FR-10) share one deterministic function.
    """
    try:
        evidence = execute(client, probe)
    except httpx.RequestError as exc:
        evidence = _unreachable_evidence(probe, exc)
    return assess_evidence(probe.kind, evidence)


def assess_evidence(probe_kind: str, evidence: Evidence) -> Verdict:
    """Map captured evidence to a verdict, purely — no network (FR-09/FR-10).

    The single deterministic assessment shared by live execution
    (:func:`run_probe`) and audit re-derivation (:mod:`revalid.audit`): the same
    ``(probe_kind, evidence)`` always yields the same verdict, so a stored verdict
    can be reproduced from its evidence alone. A ``response_status`` of 0 marks an
    unreachable target.
    """
    if evidence.response_status == 0:
        return _unreachable_verdict(evidence)
    return _ASSESSORS.get(probe_kind, assess_generic)(evidence)


def assess(evidence: Evidence) -> Verdict:
    """Map login-bypass probe evidence to a verdict (FR-09).

    still-open: HTTP 200 carrying an authentication token. fixed: HTTP 401.
    inconclusive: a 404 (endpoint relocated, not necessarily fixed) or any other
    unexpected response — never guessed as fixed.
    """
    status = evidence.response_status
    if status == 200 and _has_auth_token(evidence.response_body_excerpt):
        return Verdict(
            status=VerdictStatus.STILL_OPEN,
            reason_code="sqli_auth_bypass_succeeded",
            rationale="Injection payload returned an authenticated session token.",
            matched_indicators=("http_200", "auth_token_present"),
            evidence=evidence,
        )
    if status == 401:
        return Verdict(
            status=VerdictStatus.FIXED,
            reason_code="login_rejected",
            rationale="Injection payload was rejected with HTTP 401.",
            matched_indicators=("http_401",),
            evidence=evidence,
        )
    if status == 404:
        return Verdict(
            status=VerdictStatus.INCONCLUSIVE,
            reason_code="endpoint_changed",
            rationale="Login endpoint returned 404; cannot distinguish a fix from relocation.",
            matched_indicators=("http_404",),
            evidence=evidence,
        )
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code="unexpected_response",
        rationale=f"Unhandled response (HTTP {status}); manual review required.",
        matched_indicators=(f"http_{status}",),
        evidence=evidence,
    )


def assess_generic(evidence: Evidence) -> Verdict:
    """Assess a probe with no kind-specific matcher (FR-05 execution).

    Without a bespoke matcher every outcome is honestly *inconclusive* — generic
    indicator-matching from ``expected_indicator`` is FR-08/FR-09 work, not
    guessed here. The observed status is recorded for the audit trail.
    """
    status = evidence.response_status
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code="no_assessor",
        rationale=(
            f"No kind-specific assessor for this probe; observed HTTP {status}. "
            "Manual review required (generic matching is FR-08/FR-09)."
        ),
        matched_indicators=(f"http_{status}",),
        evidence=evidence,
    )


def assess_browser_xss(evidence: Evidence) -> Verdict:
    """Map a browser XSS probe's stored observation to a verdict (FR-14/FR-09).

    Pure over the evidence the browser recorded (:func:`revalid.browser.decode_observation`),
    so a browser verdict re-derives offline like any other (FR-10). still-open: the
    payload executed (a dialog carrying the marker fired). fixed: it neither
    executed nor survived in the DOM. inconclusive: reflected but not proven to
    execute (ambiguous — never guessed as fixed), or a malformed observation.
    """
    observation = decode_observation(evidence.response_body_excerpt)
    if observation is None:
        return Verdict(
            status=VerdictStatus.INCONCLUSIVE,
            reason_code="browser_probe_malformed",
            rationale="Browser probe evidence carried no readable observation; manual review.",
            matched_indicators=("no_observation",),
            evidence=evidence,
        )
    if observation["xss_executed"]:
        return Verdict(
            status=VerdictStatus.STILL_OPEN,
            reason_code="browser_xss_executed",
            rationale="Injected payload executed in the browser (dialog fired) — XSS still open.",
            matched_indicators=("xss_executed",),
            evidence=evidence,
        )
    if not observation.get("payload_reflected"):
        return Verdict(
            status=VerdictStatus.FIXED,
            reason_code="xss_sanitized",
            rationale="Payload neither executed nor survived in the DOM — sanitized.",
            matched_indicators=("no_execution", "not_reflected"),
            evidence=evidence,
        )
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code="xss_reflected_not_executed",
        rationale="Payload appears in the DOM but was not observed to execute; manual review.",
        matched_indicators=("reflected", "no_execution"),
        evidence=evidence,
    )


# Assessors keyed by probe kind; unknown kinds fall back to assess_generic.
_ASSESSORS: dict[str, Callable[[Evidence], Verdict]] = {
    "sqli-login-bypass": assess,
    BROWSER_XSS_KIND: assess_browser_xss,
}


def _has_auth_token(body: str) -> bool:
    """Return whether ``body`` is JSON carrying a truthy ``authentication.token``."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    auth = data.get("authentication")
    return isinstance(auth, dict) and bool(auth.get("token"))


def _unreachable_evidence(probe: Probe, exc: httpx.RequestError) -> Evidence:
    """Build the status-0 evidence recorded when a probe's target never responded."""
    return Evidence(
        request_method=probe.method,
        request_url=probe.url,
        request_body=json.dumps(probe.json_body) if probe.json_body is not None else "",
        response_status=0,
        response_body_excerpt=f"request failed: {exc}",
    )


def _unreachable_verdict(evidence: Evidence) -> Verdict:
    """Inconclusive verdict for a probe whose target never responded (status 0)."""
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code="target_unreachable",
        rationale="Probe could not reach the target; retest inconclusive.",
        matched_indicators=("no_response",),
        evidence=evidence,
    )
