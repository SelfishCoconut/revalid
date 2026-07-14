"""Demo for FR-08: the independent execution sanity checker (ADR-0014).

Usage::

    uv run python scripts/demo/execution_sanity.py

Runs fully offline. Shows the two FR-08 guarantees over the execution boundary:

1. a probe that is not in the approved plan is blocked before any request is
   sent (AC1, fail-closed);
2. a *fixed* verdict resting on a 404 (endpoint moved/absent) is forced to
   *inconclusive* with reason ``endpoint_changed`` — never *fixed* (AC2).
"""

from __future__ import annotations

import httpx

from revalid.domain import Evidence, Probe, Verdict, VerdictStatus
from revalid.sanity import PlanDeviationError, guarded_run, review_verdict


def _probe(path: str) -> Probe:
    return Probe(
        kind="planned-http",
        method="POST",
        url=f"http://localhost:3000{path}",
        expected_indicator="HTTP 200 with an auth token means still open.",
    )


def main() -> int:
    """Walk through the two FR-08 guarantees against an offline mock target."""
    approved = (_probe("/rest/user/login"),)

    # AC1: a probe outside the approved plan never opens a socket.
    def unreached(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked probe must never reach the network")  # pragma: no cover

    with httpx.Client(transport=httpx.MockTransport(unreached)) as client:
        try:
            guarded_run(client, _probe("/rest/admin"), approved)
        except PlanDeviationError as exc:
            print(f"1. plan deviation blocked: {exc} — no request sent (AC1)")

    # AC2: a 'fixed' verdict on a 404 is forced to inconclusive.
    fixed_on_404 = Verdict(
        status=VerdictStatus.FIXED,
        reason_code="login_rejected",
        matched_indicators=("http_404",),
        evidence=Evidence(
            request_method="POST",
            request_url="http://localhost:3000/rest/user/login",
            response_status=404,
        ),
    )
    reviewed = review_verdict(fixed_on_404)
    print(
        f"2. verdict claimed '{fixed_on_404.status.value}' on HTTP 404 -> forced "
        f"'{reviewed.status.value}' (reason: {reviewed.reason_code}) (AC2)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
