"""Independent execution sanity checker (FR-08, ADR-0014).

An independent verifier over the approved-plan execution boundary, sitting
between the FR-05 chokepoint and the network. It enforces two safety invariants
that no single probe assessor can guarantee on its own:

1. **Plan-deviation blocking (fail-closed).** :func:`assert_in_plan` refuses any
   probe whose canonical identity is not among the approved plan's probes — it
   logs the attempt and raises :class:`PlanDeviationError`, so nothing off-plan
   ever opens a socket.
2. **Ambiguity downgrade.** :func:`review_verdict` forces a *fixed* verdict to
   *inconclusive* when its evidence cannot distinguish a real fix from a moved or
   absent endpoint (a 404/410, or a 3xx redirect). A *fixed* verdict must rest on
   a positive rejection; the verifier only ever downgrades, never inventing
   confidence (NFR-01).

:func:`guarded_run` composes both around :func:`revalid.retest.run_probe` and is
the only path the FR-05 chokepoint uses to execute a probe.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

import httpx

from revalid.domain import Probe, Verdict, VerdictStatus
from revalid.retest import run_probe

_log = logging.getLogger(__name__)

# Statuses that make a *fixed* verdict untrustworthy: the endpoint is absent
# (moved/removed — a fix is indistinguishable from relocation). A 3xx redirect is
# handled separately as it is a different kind of non-answer.
_ABSENT_STATUSES: frozenset[int] = frozenset({404, 410})

ProbeIdentity = tuple[str, str, tuple[tuple[str, str], ...], str | None]


class PlanDeviationError(Exception):
    """Raised when a probe outside the approved plan is about to execute (FR-08 AC1)."""

    def __init__(self, probe: Probe) -> None:
        """Store the offending ``probe`` — already blocked, it never ran."""
        super().__init__(f"probe not in approved plan: {probe.method} {probe.url}")
        self.probe = probe


def probe_identity(probe: Probe) -> ProbeIdentity:
    """Return a canonical identity for plan-membership comparison.

    Two probes are the same action iff they would send the same request: same
    method, URL, headers, and JSON body. ``kind`` and ``expected_indicator`` are
    documentation, not part of what reaches the network, so they are excluded.
    """
    body = json.dumps(probe.json_body, sort_keys=True) if probe.json_body is not None else None
    return (probe.method.upper(), probe.url, tuple(sorted(probe.headers.items())), body)


def assert_in_plan(probe: Probe, approved: Iterable[Probe]) -> None:
    """Raise :class:`PlanDeviationError` if ``probe`` is not in ``approved`` (AC1).

    Fail-closed: a deviation is an integrity fault — logged and surfaced, never
    silently run or softened into a verdict.
    """
    allowed = {probe_identity(candidate) for candidate in approved}
    if probe_identity(probe) not in allowed:
        _log.warning(
            "FR-08 plan deviation blocked: %s %s is not in the approved plan",
            probe.method,
            probe.url,
        )
        raise PlanDeviationError(probe)


def review_verdict(verdict: Verdict) -> Verdict:
    """Force an over-confident *fixed* verdict to *inconclusive* (AC2).

    Only *fixed* verdicts are touched, and only downgraded — the verifier never
    manufactures confidence. A *fixed* resting on an absent endpoint (404/410)
    becomes ``endpoint_changed``; one resting on a redirect (3xx) becomes
    ``ambiguous_response``. Anything else (e.g. a 401/403 rejection) is a
    legitimate positive signal and passes through unchanged.
    """
    if verdict.status is not VerdictStatus.FIXED:
        return verdict
    status = verdict.evidence.response_status
    if status in _ABSENT_STATUSES:
        return _downgrade(
            verdict,
            "endpoint_changed",
            f"Verdict claimed fixed, but HTTP {status} means the endpoint is absent — "
            "indistinguishable from the endpoint moving. Forced inconclusive (FR-08).",
        )
    if 300 <= status < 400:
        return _downgrade(
            verdict,
            "ambiguous_response",
            f"Verdict claimed fixed, but HTTP {status} is a redirect, not an explicit "
            "rejection — the fix cannot be confirmed. Forced inconclusive (FR-08).",
        )
    return verdict


def guarded_run(client: httpx.Client, probe: Probe, approved: Iterable[Probe]) -> Verdict:
    """Execute ``probe`` under the FR-08 guarantees; the sole execution primitive.

    Blocks an off-plan probe (AC1) before any request, runs it, then reviews the
    resulting verdict for an over-confident *fixed* (AC2). ``approved`` must be a
    re-iterable collection (the caller passes the approved plan's probes).
    """
    assert_in_plan(probe, approved)
    return review_verdict(run_probe(client, probe))


def _downgrade(verdict: Verdict, reason_code: str, rationale: str) -> Verdict:
    """Return ``verdict`` forced to inconclusive with the sanity reason; evidence kept."""
    _log.info(
        "FR-08 sanity downgrade: fixed -> inconclusive (%s) on HTTP %d",
        reason_code,
        verdict.evidence.response_status,
    )
    return verdict.model_copy(
        update={
            "status": VerdictStatus.INCONCLUSIVE,
            "reason_code": reason_code,
            "rationale": rationale,
            "matched_indicators": (*verdict.matched_indicators, "fr08_downgrade"),
        }
    )
