"""Unit tests for the retest-technique registry (ADR-0019).

Covers the two new conservative HTTP assessors (access-control, sensitive-file),
the model-hint/code-normalizes kind classifier, the finding-text fallback
classifier, and the per-executor command renderers. All pure over ``Evidence`` —
no network — so they double as re-derivability (FR-10) coverage.
"""

from revalid.browser import BROWSER_XSS_KIND
from revalid.domain import Evidence, Finding, Probe, Severity, VerdictStatus
from revalid.retest import (
    ACCESS_CONTROL_KIND,
    GENERIC_KIND,
    SENSITIVE_FILE_KIND,
    SQLI_LOGIN_KIND,
    assess_access_control,
    assess_evidence,
    assess_sensitive_file,
    classify_finding_kind,
    classify_kind_from_text,
    classify_probe_kind,
    render_browser_steps,
    render_command,
    render_curl,
)


def _ev(status: int, body: str = "") -> Evidence:
    return Evidence(
        request_method="GET",
        request_url="http://localhost:3000/rest/basket/2",
        response_status=status,
        response_body_excerpt=body,
    )


# --- access-control assessor -------------------------------------------------


def test_access_control_still_open_on_200_with_body() -> None:
    verdict = assess_access_control(_ev(200, '{"data": {"id": 2}}'))
    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "unauthorized_access_succeeded"


def test_access_control_200_empty_body_is_inconclusive() -> None:
    # Conservative: a 200 with no body is not proof a resource was served.
    verdict = assess_access_control(_ev(200, "   "))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "unexpected_response"


def test_access_control_fixed_on_positive_denial() -> None:
    for status in (401, 403):
        verdict = assess_access_control(_ev(status))
        assert verdict.status is VerdictStatus.FIXED
        assert verdict.reason_code == "access_denied"


def test_access_control_absence_is_endpoint_changed() -> None:
    for status in (404, 410):
        verdict = assess_access_control(_ev(status))
        assert verdict.status is VerdictStatus.INCONCLUSIVE
        assert verdict.reason_code == "endpoint_changed"


def test_access_control_redirect_is_ambiguous() -> None:
    verdict = assess_access_control(_ev(302))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "ambiguous_redirect"


def test_access_control_other_status_is_unexpected() -> None:
    verdict = assess_access_control(_ev(500, "boom"))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "unexpected_response"


# --- sensitive-file assessor -------------------------------------------------


def test_sensitive_file_still_open_on_200_with_body() -> None:
    verdict = assess_sensitive_file(_ev(200, "root:x:0:0:"))
    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "sensitive_file_readable"


def test_sensitive_file_fixed_on_denial() -> None:
    for status in (401, 403):
        verdict = assess_sensitive_file(_ev(status))
        assert verdict.status is VerdictStatus.FIXED
        assert verdict.reason_code == "access_denied"


def test_sensitive_file_404_is_inconclusive_not_fixed() -> None:
    # A missing file cannot be told from a moved one — agrees with the FR-08 guard.
    verdict = assess_sensitive_file(_ev(404))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "endpoint_changed"


# --- registry dispatch -------------------------------------------------------


def test_assess_evidence_dispatches_by_registered_kind() -> None:
    assert assess_evidence(ACCESS_CONTROL_KIND, _ev(200, "x")).status is VerdictStatus.STILL_OPEN
    assert assess_evidence(SENSITIVE_FILE_KIND, _ev(403)).status is VerdictStatus.FIXED


def test_assess_evidence_unknown_kind_falls_back_to_generic() -> None:
    verdict = assess_evidence("no-such-technique", _ev(200, "x"))
    assert verdict.reason_code == "no_assessor"


def test_assess_evidence_unreachable_short_circuits_for_any_kind() -> None:
    verdict = assess_evidence(ACCESS_CONTROL_KIND, _ev(0))
    assert verdict.reason_code == "target_unreachable"


# --- kind classifier (model hint -> canonical HTTP kind) ---------------------


def test_classify_probe_kind_maps_synonyms() -> None:
    assert classify_probe_kind("idor") == ACCESS_CONTROL_KIND
    assert classify_probe_kind("IDOR") == ACCESS_CONTROL_KIND  # case-folded
    assert classify_probe_kind("path_traversal") == SENSITIVE_FILE_KIND  # underscore-folded
    assert classify_probe_kind("sqli") == SQLI_LOGIN_KIND
    assert classify_probe_kind(ACCESS_CONTROL_KIND) == ACCESS_CONTROL_KIND  # already canonical


def test_classify_probe_kind_refuses_browser_kind_over_http() -> None:
    # An "xss" hint names a browser technique; the HTTP gate must never assign it,
    # so it uses the fallback instead of mis-routing to the browser executor.
    assert classify_probe_kind("xss", GENERIC_KIND) == GENERIC_KIND
    assert classify_probe_kind("xss", ACCESS_CONTROL_KIND) == ACCESS_CONTROL_KIND


def test_classify_probe_kind_unknown_uses_fallback() -> None:
    assert classify_probe_kind("banana", SENSITIVE_FILE_KIND) == SENSITIVE_FILE_KIND
    assert classify_probe_kind("banana") == GENERIC_KIND


def test_classify_probe_kind_browser_fallback_downgrades_to_generic() -> None:
    assert classify_probe_kind("banana", BROWSER_XSS_KIND) == GENERIC_KIND


# --- finding-text fallback classifier ----------------------------------------


def test_classify_kind_from_text() -> None:
    assert classify_kind_from_text("SQL injection auth bypass in login") == SQLI_LOGIN_KIND
    assert classify_kind_from_text("IDOR: view another user's basket") == ACCESS_CONTROL_KIND
    assert classify_kind_from_text("Directory traversal in /ftp") == SENSITIVE_FILE_KIND
    assert classify_kind_from_text("Broken access control on admin panel") == ACCESS_CONTROL_KIND
    # Honest fallback: no clear HTTP class -> generic (XSS is browser-only, FR-14).
    assert classify_kind_from_text("Reflected XSS in the search box") == GENERIC_KIND
    assert classify_kind_from_text("Missing rate limiting") == GENERIC_KIND


def test_classify_finding_kind_uses_all_fields() -> None:
    finding = Finding(
        title="Unexpected behaviour",
        severity=Severity.HIGH,
        attack_vector="Path traversal via encoded slashes",
    )
    assert classify_finding_kind(finding) == SENSITIVE_FILE_KIND


# --- command rendering -------------------------------------------------------


def test_render_curl_get_is_faithful() -> None:
    probe = Probe(kind=ACCESS_CONTROL_KIND, method="GET", url="http://localhost:3000/rest/basket/2")
    [cmd] = render_curl(probe)
    # A special-char-free URL needs no shell quoting; the command is copy-pasteable.
    assert cmd == "curl -sS -X GET http://localhost:3000/rest/basket/2"


def test_render_curl_quotes_url_with_query_string() -> None:
    # A '?'/'&' in the query is shell-special, so shlex quotes it — still faithful.
    probe = Probe(
        kind=ACCESS_CONTROL_KIND,
        method="GET",
        url="http://localhost:3000/rest/products/search?q=a&x=1",
    )
    [cmd] = render_curl(probe)
    assert cmd == "curl -sS -X GET 'http://localhost:3000/rest/products/search?q=a&x=1'"


def test_render_curl_post_adds_content_type_and_data() -> None:
    probe = Probe(
        kind=SQLI_LOGIN_KIND,
        method="POST",
        url="http://localhost:3000/rest/user/login",
        json_body={"email": "' OR 1=1--", "password": "x"},
    )
    [cmd] = render_curl(probe)
    assert cmd.startswith("curl -sS -X POST ")
    assert "-H 'Content-Type: application/json'" in cmd
    assert "--data " in cmd


def test_render_curl_does_not_duplicate_existing_content_type() -> None:
    probe = Probe(
        kind=GENERIC_KIND,
        method="POST",
        url="http://localhost:3000/x",
        headers={"Content-Type": "text/plain"},
        json_body={"a": 1},
    )
    [cmd] = render_curl(probe)
    assert cmd.count("Content-Type") == 1
    assert "text/plain" in cmd


def test_render_command_dispatches_by_executor() -> None:
    http_probe = Probe(kind=ACCESS_CONTROL_KIND, method="GET", url="http://localhost:3000/x")
    assert render_command(http_probe)[0].startswith("curl ")

    browser_probe = Probe(
        kind=BROWSER_XSS_KIND,
        method="GET",
        url="http://localhost:3000/#/search?q=x",
        expected_indicator="a dialog fires",
    )
    steps = render_command(browser_probe)
    assert steps[0].startswith("1. Open a browser")
    assert steps == render_browser_steps(browser_probe)


def test_render_command_unknown_kind_falls_back_to_curl() -> None:
    probe = Probe(kind="mystery", method="GET", url="http://localhost:3000/x")
    assert render_command(probe)[0].startswith("curl ")


def test_render_browser_steps_without_indicator_omits_step_three() -> None:
    probe = Probe(kind=BROWSER_XSS_KIND, method="GET", url="http://localhost:3000/#/x")
    assert len(render_browser_steps(probe)) == 2
