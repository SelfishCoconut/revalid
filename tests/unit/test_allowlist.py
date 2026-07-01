"""Unit tests for the target authorization allowlist (FR-06). Pure, no I/O."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest

from revalid.allowlist import (
    DEFAULT_ALLOWLIST,
    AllowlistTransport,
    TargetGuard,
    TargetNotAllowedError,
    canonicalize,
    load_allowlist,
)
from revalid.domain import Finding, Severity


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTP://LocalHost:3000/rest", "http://localhost:3000/rest"),
        ("http://localhost:3000", "http://localhost:3000/"),
        ("http://localhost:3000/rest/../admin", "http://localhost:3000/admin"),
        ("http://localhost:3000/rest/", "http://localhost:3000/rest/"),
        ("http://localhost:3000/x#frag", "http://localhost:3000/x"),
        ("http://localhost:3000/s?q=1", "http://localhost:3000/s?q=1"),
        ("http://localhost:3000@evil/x", "http://evil/x"),
    ],
)
def test_canonicalize_normalizes(url: str, expected: str) -> None:
    assert canonicalize(url) == expected


@pytest.mark.parametrize("bad", ["localhost:3000/rest", "/just/a/path", "ftp:///x"])
def test_canonicalize_rejects_schemeless_or_hostless(bad: str) -> None:
    with pytest.raises(ValueError):
        canonicalize(bad)


def test_target_not_allowed_carries_fields() -> None:
    exc = TargetNotAllowedError("http://evil/", "not in allowlist")
    assert exc.target == "http://evil/"
    assert exc.reason == "not in allowlist"
    assert "evil" in str(exc)


def test_default_allowlist_value() -> None:
    assert DEFAULT_ALLOWLIST == frozenset({"http://localhost:3000/*"})


@pytest.mark.parametrize(
    ("pattern", "target", "expected"),
    [
        ("http://localhost:3000/", "http://localhost:3000/", True),
        ("http://localhost:3000/", "http://localhost:3000/public", False),
        ("http://localhost:3000/*", "http://localhost:3000/public/deep", True),
        ("http://localhost:3000/rest/*", "http://localhost:3000/rest/user?q=1", True),
        ("http://localhost:3000/*", "http://localhost:3001/", False),
        ("http://localhost:3000/*", "https://localhost:3000/", False),
        ("http://localhost:3000/*", "http://localhost:3000@evil/x", False),
        ("http://localhost:3000/rest/*", "http://localhost:3000/rest/../../etc", False),
    ],
)
def test_match_truth_table(pattern: str, target: str, expected: bool) -> None:
    guard = TargetGuard(frozenset({pattern}))
    assert guard.is_allowed(target) is expected


def test_is_allowed_denies_uncanonicalizable_url() -> None:
    guard = TargetGuard(frozenset({"http://localhost:3000/*"}))
    assert guard.is_allowed("not-a-url") is False


def test_is_allowed_matches_any_of_several_patterns() -> None:
    guard = TargetGuard(frozenset({"http://a:1/*", "http://b:2/*"}))
    assert guard.is_allowed("http://b:2/x") is True


def test_check_allows_silently() -> None:
    guard = TargetGuard(frozenset({"http://localhost:3000/*"}))
    assert guard.check("http://localhost:3000/rest") is None


def test_check_denied_raises_and_audits(caplog: pytest.LogCaptureFixture) -> None:
    guard = TargetGuard(frozenset({"http://localhost:3000/*"}))
    with caplog.at_level(logging.WARNING, logger="revalid.allowlist"):
        with pytest.raises(TargetNotAllowedError) as excinfo:
            guard.check("http://evil.example/")
    assert excinfo.value.target == "http://evil.example/"
    record = caplog.records[-1]
    assert record.getMessage() == "target_denied"
    assert record.target == "http://evil.example/"  # type: ignore[attr-defined]
    assert record.reason  # type: ignore[attr-defined]


def test_guard_is_immutable() -> None:
    guard = TargetGuard(frozenset({"http://localhost:3000/*"}))
    with pytest.raises(FrozenInstanceError):
        guard.patterns = frozenset()  # type: ignore[misc]


def test_load_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVALID_ALLOWLIST", raising=False)
    guard = load_allowlist()
    assert guard.patterns == DEFAULT_ALLOWLIST
    assert guard.is_allowed("http://localhost:3000/rest/products") is True


def test_load_from_explicit_path_ignores_comments_and_blanks(tmp_path: Path) -> None:
    f = tmp_path / "allow.txt"
    f.write_text("# lab targets\n\n  http://localhost:3000/*  \nhttp://localhost:8080/api/*\n")
    guard = load_allowlist(str(f))
    assert guard.patterns == frozenset({"http://localhost:3000/*", "http://localhost:8080/api/*"})


def test_load_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "allow.txt"
    f.write_text("http://localhost:9000/*\n")
    monkeypatch.setenv("REVALID_ALLOWLIST", str(f))
    guard = load_allowlist()
    assert guard.is_allowed("http://localhost:9000/x") is True


def test_explicit_path_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "env.txt"
    env_file.write_text("http://localhost:1111/*\n")
    monkeypatch.setenv("REVALID_ALLOWLIST", str(env_file))
    arg_file = tmp_path / "arg.txt"
    arg_file.write_text("http://localhost:2222/*\n")
    guard = load_allowlist(str(arg_file))
    assert guard.is_allowed("http://localhost:2222/x") is True
    assert guard.is_allowed("http://localhost:1111/x") is False


def test_load_rejects_schemeless_pattern(tmp_path: Path) -> None:
    f = tmp_path / "bad.txt"
    f.write_text("localhost:3000/*\n")
    with pytest.raises(ValueError):
        load_allowlist(str(f))


def _mock_transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="ok")

    return httpx.MockTransport(handler)


def test_transport_allows_allowlisted_request() -> None:
    calls: list[httpx.Request] = []
    guard = TargetGuard(frozenset({"http://localhost:3000/*"}))
    transport = AllowlistTransport(_mock_transport(calls), guard)
    with httpx.Client(transport=transport) as client:
        resp = client.get("http://localhost:3000/rest/products")
    assert resp.status_code == 200
    assert len(calls) == 1


def test_transport_denies_and_never_calls_inner(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[httpx.Request] = []
    guard = TargetGuard(frozenset({"http://localhost:3000/*"}))
    transport = AllowlistTransport(_mock_transport(calls), guard)
    with caplog.at_level(logging.WARNING, logger="revalid.allowlist"):
        with httpx.Client(transport=transport) as client:
            with pytest.raises(TargetNotAllowedError):
                client.get("http://169.254.169.254/latest/meta-data/")
    assert calls == []  # inner transport never touched → no socket opened
    assert caplog.records[-1].getMessage() == "target_denied"


def test_report_url_never_expands_allowlist(caplog: pytest.LogCaptureFixture) -> None:
    guard = load_allowlist()  # DEFAULT_ALLOWLIST
    before = guard.patterns
    finding = Finding(
        title="SSRF bait",
        severity=Severity.HIGH,
        affected_endpoints=("http://evil.example/",),
        raw={"url": "http://169.254.169.254/latest/meta-data/"},
    )
    hostile_urls = (*finding.affected_endpoints, str(finding.raw["url"]))
    for url in hostile_urls:
        assert guard.is_allowed(url) is False

    calls: list[httpx.Request] = []
    transport = AllowlistTransport(_mock_transport(calls), guard)
    with caplog.at_level(logging.WARNING, logger="revalid.allowlist"):
        with httpx.Client(transport=transport) as client:
            with pytest.raises(TargetNotAllowedError):
                client.get(finding.affected_endpoints[0])

    assert calls == []
    assert guard.patterns == before == DEFAULT_ALLOWLIST  # frozen: unchanged
