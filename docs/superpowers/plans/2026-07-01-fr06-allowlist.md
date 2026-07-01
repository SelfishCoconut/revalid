# FR-06 Target Authorization Allowlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an executor-level, unbypassable SSRF guard (FR-06) so no HTTP action can target a host Álvaro did not explicitly authorize, and the authorized set can never be widened by report content.

**Architecture:** One flat module `src/revalid/allowlist.py` with four small units — a pure `canonicalize`, an immutable `TargetGuard` (glob→regex matcher + fail-closed audit), an `httpx.BaseTransport` subclass that runs the guard before any socket opens, and a `load_allowlist` that builds a guard only from trusted config (path → env → built-in default). The transport is the seam FR-07's executor is forced through.

**Tech Stack:** Python 3.12+, `httpx>=0.27` (already a dependency), stdlib `urllib.parse`/`posixpath`/`re`/`logging`/`functools`/`dataclasses`. Tests via `pytest` + `httpx.MockTransport` (no real I/O). Managed with `uv`.

## Global Constraints

- Python ≥ 3.12; full type hints; `mypy --strict` must pass.
- Ruff lint + format; line length 100; Google-style docstrings on public API.
- Coverage ≥ 80 % on `src/revalid/allowlist.py`; xenon complexity ≤ C absolute.
- Module conventions (match existing `ingest.py`/`domain.py`): `from __future__ import annotations` first; module docstring naming the FR; Google docstrings.
- Unit tests are **no-I/O**: live in `tests/unit/`, use `httpx.MockTransport`, `caplog`, `tmp_path`, `monkeypatch` — never open a real socket.
- Locked design decisions D1–D5 (see spec `docs/superpowers/specs/2026-07-01-fr06-allowlist-design.md`): glob over full canonical URL, only `*` is a wildcard matching any chars incl. `/`; enforcement is the httpx transport; string-match only (no DNS resolution); denial emits a `target_denied` log event; allowlist from `REVALID_ALLOWLIST` env/file or built-in default, never from report content.
- Conventional Commits; every commit carries `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

## File Structure

- **Create** `src/revalid/allowlist.py` — the whole module (canonicalize, TargetGuard, AllowlistTransport, load_allowlist, TargetNotAllowed, DEFAULT_ALLOWLIST). One responsibility: target authorization.
- **Create** `tests/unit/test_allowlist.py` — all unit tests (matcher truth-table, load/config, immutability, transport allow/deny+audit, SSRF invariant).
- No other files change. FR-07's executor will later import `AllowlistTransport`/`load_allowlist`; that wiring is out of scope here.

---

### Task 1: Module scaffold + `canonicalize`

Delivers the pure URL-normalization core plus the shared module foundations (docstring, imports, `TargetNotAllowedError`, `DEFAULT_ALLOWLIST`) that later tasks consume.

**Files:**
- Create: `src/revalid/allowlist.py`
- Test: `tests/unit/test_allowlist.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `canonicalize(url: str) -> str` — normalized `scheme://host[:port][path][?query]`; raises `ValueError` if no scheme/host or bad port.
  - `TargetNotAllowed(Exception)` with `.target: str`, `.reason: str`.
  - `DEFAULT_ALLOWLIST: frozenset[str]` = `frozenset({"http://localhost:3000/*"})`.
  - `_normalize_path(path: str) -> str` (internal helper).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_allowlist.py`:

```python
"""Unit tests for the target authorization allowlist (FR-06). Pure, no I/O."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError

import httpx
import pytest

from revalid.allowlist import (
    DEFAULT_ALLOWLIST,
    AllowlistTransport,
    TargetGuard,
    TargetNotAllowed,
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
    exc = TargetNotAllowed("http://evil/", "not in allowlist")
    assert exc.target == "http://evil/"
    assert exc.reason == "not in allowlist"
    assert "evil" in str(exc)


def test_default_allowlist_value() -> None:
    assert DEFAULT_ALLOWLIST == frozenset({"http://localhost:3000/*"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_allowlist.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'revalid.allowlist'` (import error collects as failure).

- [ ] **Step 3: Write minimal implementation**

Create `src/revalid/allowlist.py`:

```python
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
import os
import posixpath
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from urllib.parse import urlsplit

import httpx

_LOGGER = logging.getLogger("revalid.allowlist")
_ENV_VAR = "REVALID_ALLOWLIST"

DEFAULT_ALLOWLIST: frozenset[str] = frozenset({"http://localhost:3000/*"})


class TargetNotAllowed(Exception):
    """Raised when a request targets a URL outside the configured allowlist.

    Attributes:
        target: The offending URL string.
        reason: Human-readable denial reason (routed to the audit trail).
    """

    def __init__(self, target: str, reason: str) -> None:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_allowlist.py -q`
Expected: the four Task-1 tests PASS. (Tests referencing `TargetGuard`/`AllowlistTransport`/`load_allowlist` are not written yet, so no collection error from those.)

- [ ] **Step 5: Type/lint check**

Run: `uv run mypy --strict src/revalid/allowlist.py && uv run ruff check src/revalid/allowlist.py && uv run ruff format --check src/revalid/allowlist.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/allowlist.py tests/unit/test_allowlist.py
git commit -m "feat(allowlist): add canonicalize + module scaffold (FR-06)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: `TargetGuard` — matcher + fail-closed audit

Delivers the immutable guard: glob→regex matching (only `*` special), `is_allowed`, and `check` that audits + raises on a miss.

**Files:**
- Modify: `src/revalid/allowlist.py`
- Test: `tests/unit/test_allowlist.py`

**Interfaces:**
- Consumes: `canonicalize`, `TargetNotAllowedError`, `_LOGGER` from Task 1.
- Produces:
  - `TargetGuard` — frozen dataclass, field `patterns: frozenset[str]`; `is_allowed(url: str) -> bool`; `check(url: str) -> None`.
  - `_compile_glob(pattern: str) -> re.Pattern[str]` (internal).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_allowlist.py`:

```python
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
        with pytest.raises(TargetNotAllowed) as excinfo:
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_allowlist.py -q -k "truth_table or is_allowed or check or immutable"`
Expected: FAIL — `ImportError: cannot import name 'TargetGuard'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/revalid/allowlist.py` (after `canonicalize`/`_normalize_path`):

```python
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile one allowlist glob to an anchored regex; only ``*`` is a wildcard.

    ``fnmatch`` is avoided because it also treats ``?`` and ``[...]`` as
    metacharacters, which occur literally in real URLs. Every character is
    escaped, then ``\\*`` is turned into ``.*`` (matching any run, incl. ``/``).
    """
    regex = re.escape(canonicalize(pattern)).replace(r"\*", ".*")
    return re.compile(regex)


@dataclass(frozen=True)
class TargetGuard:
    """Immutable set of allowlist globs; the sole authority on target matching.

    Attributes:
        patterns: Canonical-URL glob patterns. Built only by configuration —
            no runtime code (least of all report ingestion) can mutate it, so a
            report URL can never widen the allowlist (FR-06 AC2).
    """

    patterns: frozenset[str]

    @cached_property
    def _regexes(self) -> tuple[re.Pattern[str], ...]:
        """Compile each glob once (frozen dataclass writes the cache via __dict__)."""
        return tuple(_compile_glob(p) for p in self.patterns)

    def is_allowed(self, url: str) -> bool:
        """Return whether ``url`` matches any pattern; fail-closed on bad URLs."""
        try:
            target = canonicalize(url)
        except ValueError:
            return False
        return any(rx.fullmatch(target) is not None for rx in self._regexes)

    def check(self, url: str) -> None:
        """Return None if allowed; else emit the audit event and raise.

        Raises:
            TargetNotAllowed: If ``url`` matches no allowlist pattern.
        """
        if self.is_allowed(url):
            return
        reason = "not in allowlist"
        _LOGGER.warning("target_denied", extra={"target": url, "reason": reason})
        raise TargetNotAllowed(url, reason)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_allowlist.py -q`
Expected: all Task-1 and Task-2 tests PASS.

- [ ] **Step 5: Type/lint check**

Run: `uv run mypy --strict src/revalid/allowlist.py && uv run ruff check src/revalid/allowlist.py && uv run ruff format --check src/revalid/allowlist.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/allowlist.py tests/unit/test_allowlist.py
git commit -m "feat(allowlist): add immutable TargetGuard matcher + audit (FR-06)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: `load_allowlist` — trusted-config loading

Delivers the loader: source order (explicit path → env → default), file parsing (comments/blanks/strip), and fail-closed validation of each pattern.

**Files:**
- Modify: `src/revalid/allowlist.py`
- Test: `tests/unit/test_allowlist.py`

**Interfaces:**
- Consumes: `canonicalize`, `TargetGuard`, `DEFAULT_ALLOWLIST`, `_ENV_VAR` from Tasks 1–2.
- Produces: `load_allowlist(path: str | None = None) -> TargetGuard`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_allowlist.py`:

```python
def test_load_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVALID_ALLOWLIST", raising=False)
    guard = load_allowlist()
    assert guard.patterns == DEFAULT_ALLOWLIST
    assert guard.is_allowed("http://localhost:3000/rest/products") is True


def test_load_from_explicit_path_ignores_comments_and_blanks(tmp_path) -> None:
    f = tmp_path / "allow.txt"
    f.write_text("# lab targets\n\n  http://localhost:3000/*  \nhttp://localhost:8080/api/*\n")
    guard = load_allowlist(str(f))
    assert guard.patterns == frozenset(
        {"http://localhost:3000/*", "http://localhost:8080/api/*"}
    )


def test_load_from_env_var(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "allow.txt"
    f.write_text("http://localhost:9000/*\n")
    monkeypatch.setenv("REVALID_ALLOWLIST", str(f))
    guard = load_allowlist()
    assert guard.is_allowed("http://localhost:9000/x") is True


def test_explicit_path_overrides_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "env.txt"
    env_file.write_text("http://localhost:1111/*\n")
    monkeypatch.setenv("REVALID_ALLOWLIST", str(env_file))
    arg_file = tmp_path / "arg.txt"
    arg_file.write_text("http://localhost:2222/*\n")
    guard = load_allowlist(str(arg_file))
    assert guard.is_allowed("http://localhost:2222/x") is True
    assert guard.is_allowed("http://localhost:1111/x") is False


def test_load_rejects_schemeless_pattern(tmp_path) -> None:
    f = tmp_path / "bad.txt"
    f.write_text("localhost:3000/*\n")
    with pytest.raises(ValueError):
        load_allowlist(str(f))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_allowlist.py -q -k "load or override or schemeless"`
Expected: FAIL — `ImportError: cannot import name 'load_allowlist'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/revalid/allowlist.py` (end of module):

```python
def load_allowlist(path: str | None = None) -> TargetGuard:
    """Build a guard from trusted config only (never from report content).

    Source order: explicit ``path`` → ``$REVALID_ALLOWLIST`` → built-in
    :data:`DEFAULT_ALLOWLIST`.

    Args:
        path: Optional allowlist file path overriding the env var.

    Returns:
        A guard over the parsed, validated patterns.

    Raises:
        ValueError: If any configured pattern lacks a scheme or host.
        OSError: If a configured file path cannot be read.
    """
    source = path if path is not None else os.environ.get(_ENV_VAR)
    if source is None:
        return TargetGuard(DEFAULT_ALLOWLIST)
    return TargetGuard(frozenset(_read_patterns(source)))


def _read_patterns(path: str) -> list[str]:
    """Parse a glob-per-line file, skipping blanks/comments, validating each."""
    patterns: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        canonicalize(line)  # fail-closed: rejects a schemeless/hostless pattern
        patterns.append(line)
    return patterns
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_allowlist.py -q`
Expected: all tests through Task 3 PASS.

- [ ] **Step 5: Type/lint check**

Run: `uv run mypy --strict src/revalid/allowlist.py && uv run ruff check src/revalid/allowlist.py && uv run ruff format --check src/revalid/allowlist.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/allowlist.py tests/unit/test_allowlist.py
git commit -m "feat(allowlist): add load_allowlist trusted-config loader (FR-06)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `AllowlistTransport` + acceptance-criteria tests (AC1/AC2)

Delivers the unbypassable httpx seam and the two SRS acceptance tests: AC1 (denied host fails closed + audit) and AC2 (report URL never expands the allowlist).

**Files:**
- Modify: `src/revalid/allowlist.py`
- Test: `tests/unit/test_allowlist.py`

**Interfaces:**
- Consumes: `TargetGuard`, `TargetNotAllowedError`, `load_allowlist`, `DEFAULT_ALLOWLIST` from Tasks 1–3; `httpx`.
- Produces: `AllowlistTransport(inner: httpx.BaseTransport, guard: TargetGuard)` — subclass of `httpx.BaseTransport` with `handle_request(request: httpx.Request) -> httpx.Response`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_allowlist.py`:

```python
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
            with pytest.raises(TargetNotAllowed):
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
            with pytest.raises(TargetNotAllowed):
                client.get(finding.affected_endpoints[0])

    assert calls == []
    assert guard.patterns == before == DEFAULT_ALLOWLIST  # frozen: unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_allowlist.py -q -k "transport or report_url"`
Expected: FAIL — `ImportError: cannot import name 'AllowlistTransport'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/revalid/allowlist.py` (after `TargetGuard`, before `load_allowlist`):

```python
class AllowlistTransport(httpx.BaseTransport):
    """httpx transport that enforces the allowlist before any socket opens.

    Every request routed through the owning client passes ``guard.check`` first;
    a denied target raises :class:`TargetNotAllowedError` before the inner transport
    is touched. The executor builds its client with
    ``follow_redirects=False``, so a 3xx is captured as evidence and never
    chased — there is no redirect-hop path around the guard (design decision D2).
    """

    def __init__(self, inner: httpx.BaseTransport, guard: TargetGuard) -> None:
        self._inner = inner
        self._guard = guard

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Enforce the guard, then delegate to the inner transport."""
        self._guard.check(str(request.url))
        return self._inner.handle_request(request)
```

- [ ] **Step 4: Run the full module test suite to verify it passes**

Run: `uv run pytest tests/unit/test_allowlist.py -q`
Expected: every test PASSES.

- [ ] **Step 5: Full quality gates on the module**

Run:
```bash
uv run mypy --strict src/revalid/allowlist.py
uv run ruff check src/revalid/allowlist.py tests/unit/test_allowlist.py
uv run ruff format --check src/revalid/allowlist.py tests/unit/test_allowlist.py
uv run xenon --max-absolute C src/revalid/allowlist.py
uv run pytest tests/unit/test_allowlist.py --cov=revalid.allowlist --cov-report=term-missing
```
Expected: mypy clean; ruff clean; xenon silent (exit 0); coverage ≥ 80 % on `revalid/allowlist.py`.

- [ ] **Step 6: Run the whole suite (no regressions)**

Run: `uv run pytest -q` (or `make test` if defined)
Expected: all pre-existing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add src/revalid/allowlist.py tests/unit/test_allowlist.py
git commit -m "feat(allowlist): add unbypassable AllowlistTransport + AC1/AC2 tests (FR-06)

Closes #11.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- §3 components: `canonicalize` (T1), `TargetGuard` (T2), `AllowlistTransport` (T4), `load_allowlist` (T3), `TargetNotAllowedError`/`DEFAULT_ALLOWLIST` (T1). ✅
- §4.1 canonicalization rules → `canonicalize` + `test_canonicalize_normalizes` (scheme lowercase, port, dot-segments, trailing slash, fragment drop, userinfo defeat). ✅
- §4.2 glob→regex (only `*`) → `_compile_glob`. ✅
- §4.3 truth table → `test_match_truth_table` (all 8 rows, using the corrected Row 8: `/rest/*` vs `/rest/../../etc` → deny). ✅
- §5 enforcement flow, fail-closed, no-socket-on-deny → `test_transport_denies_and_never_calls_inner`. ✅
- §6 never-expanded invariant (frozen guard) → `test_guard_is_immutable` + `test_report_url_never_expands_allowlist`. ✅
- §7 config source order / file format / schemeless rejection / audit event → T3 tests + `test_check_denied_raises_and_audits`. ✅
- §8 testing list → covered across T1–T4. ✅
- §10 AC1 → `test_transport_denies_and_never_calls_inner`; AC2 → `test_report_url_never_expands_allowlist`; NFR-03 → transport seam. ✅

**2. Placeholder scan** — no TBD/TODO/"handle edge cases"/"similar to Task N"; every code and test step shows full content. ✅

**3. Type consistency** — names/signatures identical across tasks: `canonicalize(url: str) -> str`, `TargetGuard.patterns: frozenset[str]`, `is_allowed(url: str) -> bool`, `check(url: str) -> None`, `AllowlistTransport(inner, guard)`, `load_allowlist(path: str | None = None) -> TargetGuard`, `TargetNotAllowed(target, reason)`. `_compile_glob`/`_normalize_path`/`_read_patterns` are internal and referenced only where defined. ✅

**Note on the spec:** Row 8 of §4.3 was corrected before planning — as originally written (`http://localhost:3000/*` vs `.../a/../../etc` → ❌) it contradicted Row 3 and D1/§4.2 (`*` matches `/`, so `/etc` is under `/*`). The corrected row (`/rest/*` vs `/rest/../../etc` → ❌) preserves the intended lesson: traversal normalization stops an attacker escaping an allowed subtree. Flagged for Álvaro's async review.
