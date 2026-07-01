"""Unit tests for the target authorization allowlist (FR-06). Pure, no I/O."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError

import pytest

from revalid.allowlist import (
    DEFAULT_ALLOWLIST,
    TargetGuard,
    TargetNotAllowedError,
    canonicalize,
)


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
