"""Demo for ADR-0019: the retest-technique registry.

Shows, fully offline (no LLM, no lab), the three facets a technique bundles per
``kind``: how a finding is *classified* into a kind, how a probe *renders* as a
copy-pasteable command, and how synthetic evidence is *assessed* into a
conservative verdict.

Usage::

    uv run python scripts/demo/techniques.py
"""

from __future__ import annotations

from revalid.domain import Evidence, Probe
from revalid.retest import (
    ACCESS_CONTROL_KIND,
    SENSITIVE_FILE_KIND,
    SQLI_LOGIN_KIND,
    assess_evidence,
    classify_kind_from_text,
    render_command,
)


def _evidence(status: int, body: str = "") -> Evidence:
    return Evidence(
        request_method="GET",
        request_url="http://localhost:3000/rest/basket/2",
        response_status=status,
        response_body_excerpt=body,
    )


# (label, probe, synthetic evidence) — one per representative outcome.
_SCENARIOS: list[tuple[str, Probe, Evidence]] = [
    (
        "IDOR — another user's basket still readable",
        Probe(kind=ACCESS_CONTROL_KIND, method="GET", url="http://localhost:3000/rest/basket/2"),
        _evidence(200, '{"data": {"id": 2, "items": ["..."]}}'),
    ),
    (
        "Missing-auth — admin API now denied",
        Probe(kind=ACCESS_CONTROL_KIND, method="GET", url="http://localhost:3000/api/Users"),
        _evidence(401),
    ),
    (
        "Access control — redirect to login (ambiguous, hedged)",
        Probe(kind=ACCESS_CONTROL_KIND, method="GET", url="http://localhost:3000/rest/basket/3"),
        _evidence(302),
    ),
    (
        "Path traversal — backup file still exposed",
        Probe(
            kind=SENSITIVE_FILE_KIND,
            method="GET",
            url="http://localhost:3000/ftp/package.json.bak",
        ),
        _evidence(200, '{"name": "juice-shop", "scripts": {"...": "..."}}'),
    ),
    (
        "Path traversal — filter blocks the bypass",
        Probe(
            kind=SENSITIVE_FILE_KIND,
            method="GET",
            url="http://localhost:3000/ftp/coupons_2013.md.bak",
        ),
        _evidence(403),
    ),
    (
        "SQLi login bypass — token returned",
        Probe(
            kind=SQLI_LOGIN_KIND,
            method="POST",
            url="http://localhost:3000/rest/user/login",
            json_body={"email": "' OR 1=1--", "password": "x"},
        ),
        _evidence(200, '{"authentication": {"token": "eyJ..."}}'),
    ),
]

# Finding titles -> the FR-04 fallback classifier (a real report's phrasings).
_TITLES: tuple[str, ...] = (
    "IDOR: view another user's basket",
    "Directory traversal exposes /ftp backup files",
    "Broken access control on the admin panel",
    "SQL injection auth bypass in login",
    "Reflected XSS in the search box",  # -> generic: XSS is browser-only (FR-14)
)


def main() -> int:
    """Print classification, rendering, and verdicts for the seed techniques."""
    print("== FR-04 classification (finding text -> technique kind) ==")
    for title in _TITLES:
        print(f"  {classify_kind_from_text(title):24s} <- {title}")

    print("\n== assessors + command rendering (offline, synthetic evidence) ==")
    for label, probe, evidence in _SCENARIOS:
        verdict = assess_evidence(probe.kind, evidence)
        print(f"\n{label}")
        print(f"  kind:    {probe.kind}")
        print(f"  command: {render_command(probe)[0]}")
        print(
            f"  HTTP {evidence.response_status} -> {verdict.status.value} ({verdict.reason_code})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
