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

import httpx

from revalid.allowlist import AllowlistTransport, TargetGuard
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
    every retest still produces an evidence-backed verdict (FR-09).
    """
    try:
        evidence = execute(client, probe)
    except httpx.RequestError as exc:
        return _unreachable_verdict(probe, exc)
    return assess(evidence)


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


def _unreachable_verdict(probe: Probe, exc: httpx.RequestError) -> Verdict:
    """Build an inconclusive verdict for a probe whose target never responded."""
    evidence = Evidence(
        request_method=probe.method,
        request_url=probe.url,
        request_body=json.dumps(probe.json_body) if probe.json_body is not None else "",
        response_status=0,
        response_body_excerpt=f"request failed: {exc}",
    )
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code="target_unreachable",
        rationale="Probe could not reach the target; retest inconclusive.",
        matched_indicators=("no_response",),
        evidence=evidence,
    )
