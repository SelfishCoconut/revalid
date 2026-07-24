"""Retest scope parsing (FR-17 / FR-06, issue #208).

The retest scope is written by the operator at launch as one or more target
endpoints (or defaulted from the finding's affected endpoints). The sandbox is
provisioned against the **host** of that scope, not the specific page: a finding
about ``https://domain.com/#/login`` retests ``domain.com`` at any path, so the
agent can follow the vulnerability wherever it lives under that host — not only
the one URL in the report.

This module holds the pure parsing: an endpoint string in, its host (``host`` or
``host:port``) out. Provisioning (lab vs. online egress) lives in the sandbox.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = ["scope_host", "scope_hosts"]


def scope_host(endpoint: str) -> str | None:
    """Parse one scope endpoint down to its host (``host`` or ``host:port``).

    Keeps the port (it is part of the reachable target — the lab is
    ``localhost:3000``) but drops scheme, userinfo, path, query and fragment,
    including SPA hash routes. Sub-domains are preserved (they are distinct
    hosts); only the path is stripped.

    Args:
        endpoint: A target string — a full URL, a scheme-less ``host/path`` or a
            bare ``host:port``. SPA hash routes (``domain.com/#/login``) are
            handled.

    Returns:
        The lower-cased ``host`` (or ``host:port``), or ``None`` when the string
        carries no parseable host.

    Examples:
        >>> scope_host("https://domain.com/#/login")
        'domain.com'
        >>> scope_host("http://domain.com:8080/a/b?x=1")
        'domain.com:8080'
        >>> scope_host("domain.com/login")
        'domain.com'
        >>> scope_host("http://localhost:3000/rest/user/login")
        'localhost:3000'
        >>> scope_host("   ") is None
        True
    """
    raw = endpoint.strip()
    if not raw:
        return None
    # A scheme-less input ("domain.com/login") parses with an empty netloc, so
    # force the authority form; a real scheme already yields the netloc.
    if "://" not in raw:
        raw = "//" + raw.lstrip("/")
    netloc = urlsplit(raw).netloc
    # Strip any userinfo ("user:pass@host") — the authority is host[:port].
    host = netloc.rsplit("@", 1)[-1].strip()
    return host.lower() or None


def scope_hosts(endpoints: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a scope's endpoints to a de-duplicated, order-preserving host tuple.

    Args:
        endpoints: The launch-time scope endpoints (``target_set``).

    Returns:
        Each distinct parseable host, first-seen order preserved. Unparseable
        entries are dropped.
    """
    seen: dict[str, None] = {}
    for endpoint in endpoints:
        host = scope_host(endpoint)
        if host is not None and host not in seen:
            seen[host] = None
    return tuple(seen)
