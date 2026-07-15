"""Retest engine: execute a verification probe and derive a verdict (FR-07/FR-09).

A probe is executed over the FR-06 :class:`AllowlistTransport` so no request can
reach an unauthorized target; executing it captures request/response evidence.
Evidence is mapped to a still-open / fixed / inconclusive :class:`Verdict` by a
**technique registry** (ADR-0019): each probe ``kind`` binds a pure
``Evidence -> Verdict`` assessor, a command renderer (its executor's idiom), and
an executor class, so a new web-testing technique is one :func:`register_technique`
entry. Unknown kinds fall back to an honest inconclusive (:func:`assess_generic`).
Assessors take nothing beyond the evidence, so every verdict re-derives offline
(FR-10). The FR-04 planner tags probes via :func:`classify_probe_kind`.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from revalid.allowlist import AllowlistTransport, TargetGuard
from revalid.browser import BROWSER_XSS_KIND, decode_observation
from revalid.domain import Evidence, Finding, Probe, Verdict, VerdictStatus

_LAB_ENV = "REVALID_LAB_BASE_URL"
DEFAULT_LAB_BASE_URL = "http://localhost:3000"

# Juice Shop's canonical auth-bypass payload: a tautology in the email field
# that collapses the login WHERE-clause. Verification-only — it reads back an
# existing session, it does not modify data.
_SQLI_LOGIN_EMAIL = "' OR 1=1--"
_BODY_EXCERPT_LIMIT = 16_384

# Canonical technique kinds (ADR-0019). The FR-04 classifier only ever assigns
# HTTP-executor kinds; browser kinds are built by their own probe builders
# (``revalid.browser``), never by generic planning.
SQLI_LOGIN_KIND = "sqli-login-bypass"
ACCESS_CONTROL_KIND = "access-control"
SENSITIVE_FILE_KIND = "sensitive-file-exposure"
GENERIC_KIND = "planned-http"

# A *fixed* verdict for the access-control / sensitive-file techniques may rest
# only on a positive denial (an explicit reject), never on an absence — the
# conservative-assessment rule (ADR-0019), which also keeps these assessors in
# agreement with the FR-08 sanity guard.
_DENIAL_STATUSES: frozenset[int] = frozenset({401, 403})
_ABSENT_STATUSES: frozenset[int] = frozenset({404, 410})


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
    technique = _TECHNIQUES.get(probe_kind)
    assessor = technique.assess if technique is not None else assess_generic
    return assessor(evidence)


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


def assess_access_control(evidence: Evidence) -> Verdict:
    """Assess a broken-access-control probe — IDOR/BOLA, missing-auth, admin (ADR-0019).

    From HTTP evidence these are one decision: was a should-be-denied request
    served or denied? Conservative — still-open only on a served resource (200
    with a body), fixed only on a positive denial (401/403); every ambiguous
    signal is inconclusive, so a mis-assigned kind can never yield a wrong
    confident verdict (NFR-01).
    """
    status = evidence.response_status
    if status == 200 and evidence.response_body_excerpt.strip():
        return Verdict(
            status=VerdictStatus.STILL_OPEN,
            reason_code="unauthorized_access_succeeded",
            rationale="A request that should be denied returned HTTP 200 with a resource body.",
            matched_indicators=("http_200", "resource_returned"),
            evidence=evidence,
        )
    if status in _DENIAL_STATUSES:
        return _denied_verdict(evidence)
    return _ambiguous_access_verdict(evidence)


def assess_sensitive_file(evidence: Evidence) -> Verdict:
    """Assess a sensitive-file / path-traversal probe, conservatively (ADR-0019).

    still-open: 200 with a file body. fixed: a positive denial (401/403 — e.g. the
    traversal filter blocking). Absence (404/410) or anything else is
    inconclusive: a missing file cannot be told apart from a moved one, which is
    exactly the FR-08 stance, so assessor and sanity guard never disagree.
    """
    status = evidence.response_status
    if status == 200 and evidence.response_body_excerpt.strip():
        return Verdict(
            status=VerdictStatus.STILL_OPEN,
            reason_code="sensitive_file_readable",
            rationale="A protected file path returned HTTP 200 with content — still readable.",
            matched_indicators=("http_200", "file_returned"),
            evidence=evidence,
        )
    if status in _DENIAL_STATUSES:
        return _denied_verdict(evidence)
    return _ambiguous_access_verdict(evidence)


def _denied_verdict(evidence: Evidence) -> Verdict:
    """A *fixed* verdict resting on a positive denial (401/403)."""
    status = evidence.response_status
    return Verdict(
        status=VerdictStatus.FIXED,
        reason_code="access_denied",
        rationale=f"Access was positively denied (HTTP {status}); the control holds.",
        matched_indicators=(f"http_{status}",),
        evidence=evidence,
    )


def _ambiguous_access_verdict(evidence: Evidence) -> Verdict:
    """An *inconclusive* verdict for any signal that is neither a clear allow nor deny."""
    status = evidence.response_status
    if status in _ABSENT_STATUSES:
        reason, note = "endpoint_changed", "the endpoint is absent — indistinguishable from a move"
    elif 300 <= status < 400:
        reason, note = "ambiguous_redirect", "a redirect is not an explicit allow or deny"
    else:
        reason, note = "unexpected_response", "the response is not a clear allow or deny"
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code=reason,
        rationale=f"Inconclusive (HTTP {status}): {note}; manual review.",
        matched_indicators=(f"http_{status}",),
        evidence=evidence,
    )


def _has_header(headers: dict[str, str], name: str) -> bool:
    """Return whether ``headers`` already carries ``name`` (case-insensitive)."""
    return any(key.lower() == name.lower() for key in headers)


def render_curl(probe: Probe) -> list[str]:
    """Render an HTTP probe as a faithful, copy-pasteable ``curl`` command (ADR-0019).

    A 1:1 rendering of the exact request the executor sends — nothing added or
    hidden — so approving the command approves the bytes on the wire. Returned as
    a single-element list to share the ``Probe -> list[str]`` renderer shape with
    multi-step browser rendering.
    """
    headers = dict(probe.headers)
    if probe.json_body is not None and not _has_header(headers, "content-type"):
        headers["Content-Type"] = "application/json"
    parts = ["curl", "-sS", "-X", probe.method.upper(), shlex.quote(probe.url)]
    for key, value in headers.items():
        parts += ["-H", shlex.quote(f"{key}: {value}")]
    if probe.json_body is not None:
        parts += ["--data", shlex.quote(json.dumps(probe.json_body))]
    return [" ".join(parts)]


def render_browser_steps(probe: Probe) -> list[str]:
    """Render a browser probe as ordered manual steps in the browser idiom (ADR-0019)."""
    steps = [
        f"1. Open a browser at {probe.url}",
        "2. Watch for the injected payload executing (a dialog carrying the probe marker).",
    ]
    if probe.expected_indicator:
        steps.append(f"3. Read the result as: {probe.expected_indicator}")
    return steps


@dataclass(frozen=True)
class Technique:
    """One retest technique — assessor + command renderer + executor, keyed by kind.

    The single extension point (ADR-0019): registering a technique makes its
    ``kind`` assessable, renderable, and — for HTTP kinds — assignable by the
    FR-04 classifier. Unregistered kinds fall back to the generic assessor and
    curl rendering, never an error.

    Attributes:
        kind: Stable slug persisted on probes and verdicts (the dispatch key).
        label: Human-readable class name for reporting/UI.
        executor: ``"http"`` (FR-07) or ``"browser"`` (FR-14) — how it runs.
        assess: Pure ``Evidence -> Verdict`` matcher (FR-09/FR-10).
        render: ``Probe -> list[str]`` command rendering in the executor's idiom.
        aliases: Normalized synonyms the FR-04 classifier maps to this kind.
    """

    kind: str
    label: str
    executor: str
    assess: Callable[[Evidence], Verdict]
    render: Callable[[Probe], list[str]]
    aliases: tuple[str, ...] = ()


_TECHNIQUES: dict[str, Technique] = {}


def register_technique(technique: Technique) -> None:
    """Add (or replace) a technique in the registry — the ADR-0019 extension seam."""
    _TECHNIQUES[technique.kind] = technique


register_technique(
    Technique(
        SQLI_LOGIN_KIND,
        "SQLi login bypass",
        "http",
        assess,
        render_curl,
        aliases=("sqli", "sql-injection", "login-bypass"),
    )
)
register_technique(
    Technique(
        ACCESS_CONTROL_KIND,
        "Broken access control",
        "http",
        assess_access_control,
        render_curl,
        aliases=(
            "idor",
            "bola",
            "insecure-direct-object-reference",
            "broken-access-control",
            "missing-auth",
            "missing-authentication",
            "authorization",
            "admin",
            "admin-access",
            "privilege-escalation",
            "basket-access",
            "forced-browsing",
        ),
    )
)
register_technique(
    Technique(
        SENSITIVE_FILE_KIND,
        "Sensitive file exposure",
        "http",
        assess_sensitive_file,
        render_curl,
        aliases=(
            "directory-traversal",
            "path-traversal",
            "traversal",
            "sensitive-file",
            "file-exposure",
            "backup-file",
            "lfi",
            "arbitrary-file-read",
            "exposed-file",
        ),
    )
)
register_technique(
    Technique(
        BROWSER_XSS_KIND,
        "DOM/stored XSS (browser)",
        "browser",
        assess_browser_xss,
        render_browser_steps,
        aliases=("xss", "dom-xss", "stored-xss", "reflected-xss"),
    )
)


def render_command(probe: Probe) -> list[str]:
    """Render ``probe`` as human-readable command lines in its executor's idiom (ADR-0019)."""
    technique = _TECHNIQUES.get(probe.kind)
    render = technique.render if technique is not None else render_curl
    return render(probe)


def _normalize_kind(value: str) -> str:
    """Fold a proposed kind hint to the registry's kebab-case slug form."""
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _lookup_kind(normalized: str) -> str:
    """Return the registered kind a normalized slug names or aliases, or ``""``."""
    for technique in _TECHNIQUES.values():
        if normalized == technique.kind or normalized in technique.aliases:
            return technique.kind
    return ""


def _is_http_kind(kind: str) -> bool:
    """Return whether ``kind`` runs over HTTP (so the FR-04 gate may assign it)."""
    if kind == GENERIC_KIND:
        return True
    technique = _TECHNIQUES.get(kind)
    return technique is not None and technique.executor == "http"


def classify_probe_kind(proposed: str, fallback: str = GENERIC_KIND) -> str:
    """Normalize a model-proposed kind hint to a registered HTTP technique (ADR-0019).

    Code is authoritative over the model's hint. Only HTTP-executor kinds are ever
    returned — a browser kind (e.g. an ``xss`` hint on an HTTP action) is refused
    so it can never mis-route to the browser executor. An unrecognized hint uses
    ``fallback`` (itself constrained to an HTTP kind), else the generic kind.
    """
    kind = _lookup_kind(_normalize_kind(proposed))
    if kind and _is_http_kind(kind):
        return kind
    return fallback if _is_http_kind(fallback) else GENERIC_KIND


_SENSITIVE_FILE_HINTS = (
    "traversal",
    "sensitive file",
    "backup",
    "/ftp",
    "arbitrary file",
    "file disclosure",
    "file read",
    "lfi",
    "exposed file",
)
_ACCESS_CONTROL_HINTS = (
    "idor",
    "insecure direct object",
    "bola",
    "broken access",
    "access control",
    "unauthorized",
    "authorization",
    "privilege",
    "admin",
    "basket",
    "forced brows",
    "missing auth",
)


def classify_kind_from_text(text: str) -> str:
    """Best-effort keyword classification of finding text into an HTTP kind (ADR-0019).

    The *fallback* used when the model gives no kind hint. Deliberately
    conservative: unmatched text stays :data:`GENERIC_KIND` (an honest
    inconclusive) rather than being forced into a technique.
    """
    blob = text.lower()
    if ("sql" in blob or "injection" in blob) and ("login" in blob or "sign in" in blob):
        return SQLI_LOGIN_KIND
    if any(hint in blob for hint in _SENSITIVE_FILE_HINTS):
        return SENSITIVE_FILE_KIND
    if any(hint in blob for hint in _ACCESS_CONTROL_HINTS):
        return ACCESS_CONTROL_KIND
    return GENERIC_KIND


def classify_finding_kind(finding: Finding) -> str:
    """Classify a whole finding (title, description, attack vector, endpoints)."""
    parts = (finding.title, finding.description, finding.attack_vector, *finding.affected_endpoints)
    return classify_kind_from_text(" ".join(parts))


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
