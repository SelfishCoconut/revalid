"""Unit tests for the retest engine — verdict logic and capture (FR-07/FR-09).

No network: the executor is driven through ``httpx.MockTransport`` and the
verdict engine is exercised on synthetic evidence.
"""

from collections.abc import Callable

import httpx
import pytest

from revalid.domain import Evidence, VerdictStatus
from revalid.retest import (
    DEFAULT_LAB_BASE_URL,
    assess,
    execute,
    lab_base_url,
    login_sqli_probe,
    run_probe,
)

_TOKEN_BODY = '{"authentication": {"token": "eyJhbGciOi.payload.sig", "umail": "a@b.c"}}'


def _evidence(status: int, body: str = "") -> Evidence:
    return Evidence(
        request_method="POST",
        request_url=f"{DEFAULT_LAB_BASE_URL}/rest/user/login",
        response_status=status,
        response_body_excerpt=body,
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_assess_still_open_when_token_returned() -> None:
    verdict = assess(_evidence(200, _TOKEN_BODY))
    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "sqli_auth_bypass_succeeded"
    assert "auth_token_present" in verdict.matched_indicators
    assert verdict.evidence.response_status == 200


def test_assess_fixed_on_401() -> None:
    verdict = assess(_evidence(401, '{"error": "Invalid email or password."}'))
    assert verdict.status is VerdictStatus.FIXED
    assert verdict.reason_code == "login_rejected"


def test_assess_inconclusive_endpoint_changed_on_404() -> None:
    verdict = assess(_evidence(404))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "endpoint_changed"


def test_assess_inconclusive_on_200_without_token() -> None:
    verdict = assess(_evidence(200, '{"authentication": {}}'))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "unexpected_response"


@pytest.mark.parametrize("body", ["<html>not json</html>", "", "[]", '"a string"', "42"])
def test_assess_inconclusive_on_200_with_non_object_body(body: str) -> None:
    # The verdict-critical "never guess still_open" path: a 200 whose body is not
    # a JSON object carrying a token must not read as still_open.
    verdict = assess(_evidence(200, body))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "unexpected_response"


def test_assess_inconclusive_on_unexpected_status() -> None:
    verdict = assess(_evidence(500, "boom"))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "unexpected_response"
    assert verdict.matched_indicators == ("http_500",)


@pytest.mark.parametrize(
    ("status", "body"),
    [(200, _TOKEN_BODY), (401, ""), (404, ""), (500, "x"), (200, "{}")],
)
def test_every_verdict_carries_reason_code_and_evidence(status: int, body: str) -> None:
    # FR-09: no verdict without linked evidence; inconclusive needs a reason code.
    verdict = assess(_evidence(status, body))
    assert verdict.reason_code
    assert verdict.evidence.response_status == status


def test_execute_captures_request_and_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/rest/user/login"
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with _client(handler) as client:
        evidence = execute(client, login_sqli_probe(DEFAULT_LAB_BASE_URL))
    assert evidence.request_method == "POST"
    assert evidence.response_status == 200
    assert "token" in evidence.response_body_excerpt
    assert evidence.elapsed_ms >= 0.0


def test_run_probe_unreachable_is_inconclusive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _client(handler) as client:
        verdict = run_probe(client, login_sqli_probe(DEFAULT_LAB_BASE_URL))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "target_unreachable"
    assert verdict.evidence.response_status == 0


def test_login_sqli_probe_shape() -> None:
    probe = login_sqli_probe("http://localhost:3000/")
    assert probe.kind == "sqli-login-bypass"
    assert probe.method == "POST"
    assert probe.url == "http://localhost:3000/rest/user/login"
    assert probe.json_body == {"email": "' OR 1=1--", "password": "x"}


def test_lab_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVALID_LAB_BASE_URL", raising=False)
    assert lab_base_url() == DEFAULT_LAB_BASE_URL
    monkeypatch.setenv("REVALID_LAB_BASE_URL", "http://localhost:9999")
    assert lab_base_url() == "http://localhost:9999"


def test_assess_generic_is_inconclusive_with_no_assessor_reason() -> None:
    from revalid.retest import assess_generic

    verdict = assess_generic(_evidence(200, "irrelevant"))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "no_assessor"
    assert "http_200" in verdict.matched_indicators


def test_run_probe_dispatches_unknown_kind_to_generic() -> None:
    from revalid.domain import Probe

    probe = Probe(kind="planned-http", method="GET", url="http://localhost:3000/rest/x")
    verdict = run_probe(_client(lambda _r: httpx.Response(200, text="ok")), probe)
    assert verdict.reason_code == "no_assessor"
