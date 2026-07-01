"""Target authorization allowlist — executor-level SSRF guard (FR-06).

A pentest report is untrusted input: a finding may name an internal or
metadata host. This module guarantees no HTTP action reaches a target Álvaro
did not explicitly authorize, and that the authorized set is built only from
trusted configuration — never from report content. Enforcement is an
unbypassable :class:`AllowlistTransport`; matching is on the canonical URL
string (no DNS resolution, per design decision D3).
"""

from __future__ import annotations

import logging
import posixpath
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("revalid.allowlist")
_ENV_VAR = "REVALID_ALLOWLIST"

DEFAULT_ALLOWLIST: frozenset[str] = frozenset({"http://localhost:3000/*"})


class TargetNotAllowedError(Exception):
    """Raised when a request targets a URL outside the configured allowlist.

    Attributes:
        target: The offending URL string.
        reason: Human-readable denial reason (routed to the audit trail).
    """

    def __init__(self, target: str, reason: str) -> None:
        """Store the offending ``target`` and denial ``reason``."""
        super().__init__(f"target not allowed: {target} ({reason})")
        self.target = target
        self.reason = reason


def canonicalize(url: str) -> str:
    """Normalize a URL (or allowlist glob) to one canonical comparison string.

    Applied identically to request URLs and allowlist patterns so matching
    compares like with like. Scheme and host are lowercased; the host is taken
    from ``.hostname`` (defeating the ``user:pass@host`` userinfo trick); an
    explicit port is preserved but default ports are not synthesized;
    dot-segments are resolved; the fragment is dropped.

    Args:
        url: An absolute URL or allowlist glob.

    Returns:
        ``scheme://host[:port][path][?query]``.

    Raises:
        ValueError: If the URL lacks a scheme or host, or has an invalid port.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        raise ValueError(f"URL must have a scheme and host: {url!r}")
    port = f":{parts.port}" if parts.port is not None else ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{host}{port}{_normalize_path(parts.path)}{query}"


def _normalize_path(path: str) -> str:
    """Resolve dot-segments, preserving a meaningful trailing slash."""
    if not path:
        return "/"
    normalized = posixpath.normpath(path)
    if normalized == "/":
        return "/"
    if path.endswith("/"):
        normalized += "/"
    return normalized
