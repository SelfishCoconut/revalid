"""Unit tests for the target authorization allowlist (FR-06). Pure, no I/O."""

from __future__ import annotations

import pytest

from revalid.allowlist import (
    DEFAULT_ALLOWLIST,
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
