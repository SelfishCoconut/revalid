"""Unit tests for retest scope parsing (issue #208)."""

import pytest

from revalid.scope import scope_host, scope_hosts


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        # The motivating case: an SPA hash route resolves to the host, not the page.
        ("https://domain.com/#/login", "domain.com"),
        # Scheme, explicit port, path and query -> host:port kept, the rest dropped.
        ("http://domain.com:8080/a/b?x=1", "domain.com:8080"),
        # Scheme-less host/path.
        ("domain.com/login", "domain.com"),
        # The lab target: the port is part of the reachable host.
        ("http://localhost:3000/rest/user/login", "localhost:3000"),
        # Sub-domains are distinct hosts and preserved.
        ("https://api.sub.domain.com/v1/orders", "api.sub.domain.com"),
        # Userinfo is stripped; the authority is host[:port].
        ("http://user:pass@domain.com/x", "domain.com"),
        # A bare host passes through.
        ("domain.com", "domain.com"),
        # Case is normalised.
        ("HTTPS://Domain.COM/Path", "domain.com"),
    ],
)
def test_scope_host_parses_to_the_host(endpoint: str, expected: str) -> None:
    assert scope_host(endpoint) == expected


@pytest.mark.parametrize("endpoint", ["", "   ", "\n\t"])
def test_scope_host_returns_none_without_a_host(endpoint: str) -> None:
    assert scope_host(endpoint) is None


def test_scope_hosts_dedupes_preserving_order() -> None:
    endpoints = (
        "https://a.com/x",
        "http://a.com/#/y",  # same host as the first -> collapsed
        "b.com/z",
    )
    assert scope_hosts(endpoints) == ("a.com", "b.com")


def test_scope_hosts_drops_unparseable_entries() -> None:
    assert scope_hosts(("", "  ", "a.com")) == ("a.com",)


def test_scope_hosts_empty() -> None:
    assert scope_hosts(()) == ()
