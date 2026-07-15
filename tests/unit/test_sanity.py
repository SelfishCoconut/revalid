"""Unit tests for the FR-08 execution sanity checker (ADR-0014).

The two acceptance criteria:
- AC1: an off-plan probe is blocked and logged, never reaching the network.
- AC2: a *fixed* verdict on an endpoint-moved (404) response is forced to
  *inconclusive* with reason ``endpoint_changed`` — never *fixed*.
"""

import logging

import pytest

from revalid.domain import Evidence, Probe, Verdict, VerdictStatus
from revalid.sanity import (
    PlanDeviationError,
    assert_in_plan,
    guarded_run,
    probe_identity,
    review_verdict,
)

_LOGIN = "http://localhost:3000/rest/user/login"


def _probe(
    *,
    url: str = _LOGIN,
    method: str = "POST",
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Probe:
    return Probe(
        kind="planned-http",
        method=method,
        url=url,
        headers=headers or {},
        json_body=json_body,
        expected_indicator="x",
    )


def _verdict(status: VerdictStatus, response_status: int, reason_code: str = "assessor") -> Verdict:
    return Verdict(
        status=status,
        reason_code=reason_code,
        rationale="r",
        matched_indicators=(f"http_{response_status}",),
        evidence=Evidence(
            request_method="POST", request_url=_LOGIN, response_status=response_status
        ),
    )


# --- AC1: plan-deviation blocking -----------------------------------------


def test_off_plan_probe_is_blocked_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    approved = (_probe(),)
    intruder = _probe(url="http://localhost:3000/rest/admin")
    with caplog.at_level(logging.WARNING), pytest.raises(PlanDeviationError):
        assert_in_plan(intruder, approved)
    assert any("deviation" in record.message.lower() for record in caplog.records)


def test_guarded_run_blocks_off_plan_before_any_request() -> None:
    calls: list[Probe] = []

    def execute(probe: Probe) -> Verdict:
        calls.append(probe)
        return _verdict(VerdictStatus.STILL_OPEN, 200)

    with pytest.raises(PlanDeviationError):
        guarded_run(_probe(url="http://localhost:3000/rest/admin"), (_probe(),), execute)
    assert calls == []  # AC1: the executor is never called for an off-plan probe


def test_in_plan_probe_passes() -> None:
    assert_in_plan(_probe(), (_probe(),))  # identical action → no raise


def test_probe_identity_distinguishes_method_url_and_body() -> None:
    base = _probe()
    assert probe_identity(base) == probe_identity(_probe())
    assert probe_identity(base) != probe_identity(_probe(method="GET"))
    assert probe_identity(base) != probe_identity(_probe(url="http://localhost:3000/other"))
    assert probe_identity(base) != probe_identity(_probe(json_body={"a": 1}))


# --- AC2: ambiguity downgrade ---------------------------------------------


def test_fixed_on_404_forced_inconclusive_endpoint_changed() -> None:
    reviewed = review_verdict(_verdict(VerdictStatus.FIXED, 404, reason_code="login_rejected"))
    assert reviewed.status is VerdictStatus.INCONCLUSIVE
    assert reviewed.reason_code == "endpoint_changed"  # AC2: never fixed


def test_fixed_on_410_forced_inconclusive() -> None:
    reviewed = review_verdict(_verdict(VerdictStatus.FIXED, 410))
    assert reviewed.status is VerdictStatus.INCONCLUSIVE
    assert reviewed.reason_code == "endpoint_changed"


def test_fixed_on_redirect_forced_inconclusive_ambiguous() -> None:
    reviewed = review_verdict(_verdict(VerdictStatus.FIXED, 302))
    assert reviewed.status is VerdictStatus.INCONCLUSIVE
    assert reviewed.reason_code == "ambiguous_response"


def test_legit_fixed_on_401_is_preserved() -> None:
    fixed = _verdict(VerdictStatus.FIXED, 401, reason_code="login_rejected")
    assert review_verdict(fixed) == fixed  # positive rejection: a real fix stands


@pytest.mark.parametrize("status", [VerdictStatus.STILL_OPEN, VerdictStatus.INCONCLUSIVE])
def test_non_fixed_verdicts_are_untouched(status: VerdictStatus) -> None:
    verdict = _verdict(status, 404)
    assert review_verdict(verdict) == verdict  # verifier only ever downgrades fixed


def test_downgrade_preserves_evidence_and_marks_the_indicator() -> None:
    original = _verdict(VerdictStatus.FIXED, 404)
    reviewed = review_verdict(original)
    assert reviewed.evidence == original.evidence  # evidence is never rewritten
    assert "fr08_downgrade" in reviewed.matched_indicators


def test_review_is_idempotent() -> None:
    once = review_verdict(_verdict(VerdictStatus.FIXED, 404))
    assert review_verdict(once) == once  # already inconclusive → unchanged


def test_guarded_run_downgrades_a_fixed_on_404_end_to_end() -> None:
    probe = _probe()  # in-plan

    # An executor that (wrongly) returns fixed on a 404: the guard must still
    # downgrade it, proving no fixed survives an absent endpoint regardless of the
    # executor or probe kind.
    def execute(_probe: Probe) -> Verdict:
        return _verdict(VerdictStatus.FIXED, 404, reason_code="login_rejected")

    verdict = guarded_run(probe, (probe,), execute)
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "endpoint_changed"
