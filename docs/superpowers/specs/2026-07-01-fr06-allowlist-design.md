# FR-06 — Target authorization allowlist · design spec

- **Date**: 2026-07-01
- **Requirement**: FR-06 (SRS §3) — priority *Must*. Issue **#11**. Milestone **M1**.
- **Related**: ADR-0002 (stack: FastAPI + httpx + SQLite); NFR-03 (executor-level
  enforcement, localhost bind, non-destructive probes); NFR-04 (data protection).
- **Status**: accepted — Álvaro approved D1–D5 (D3 confirmed: string-match only,
  no DNS-rebinding defense) on 2026-07-01. Cleared for implementation.

## 1. Context & problem

The retest executor (FR-07, not yet built) will issue HTTP requests derived from
pentest-report findings. A report is **untrusted input**: a finding could name
`http://internal.company/` or `http://169.254.169.254/` and, if executed, turn the
tool into an SSRF vector. FR-06 must guarantee that **no action can target a host
that Álvaro did not explicitly authorize**, and that the authorized set can **never
be widened by report content** — only by trusted configuration.

FR-06 lands *before* the executor exists, so it ships the **guard and the seam**
the executor is forced through; FR-07 plugs into it.

Acceptance criteria (from the SRS):
- **AC1** — an approved plan referencing a non-allowlisted host **fails closed**
  with an audit-trail entry.
- **AC2** — report-supplied URLs **never expand** the allowlist (SSRF guard test).

## 2. Locked decisions

From the design dialogue (2026-07-01):

| # | Decision | Rationale |
|---|---|---|
| D1 | Allowlist entries are **glob patterns over the full canonical URL** `scheme://host[:port]/path[?query]`. `*` matches any characters *including* `/`; no `*` ⇒ exact match. | Least-privilege, breadth is explicit. `localhost/` ≠ `localhost/*`. |
| D2 | Enforcement is an **unbypassable httpx transport**. The executor's client is built with it; there is no network path around the guard. Redirects are **not auto-followed**. | Structural guarantee, not developer discipline; kills redirect-hop SSRF. |
| D3 | Matching is on the **canonicalized URL string**; no DNS resolution, `localhost` ≢ `127.0.0.1`. | Threat is report URLs, not DNS-rebinding of trusted local hosts. Deterministic, dependency-free. Revisit if the threat model grows. |
| D4 | On denial: emit a **structured audit log event** now; durable persistence deferred to **FR-10** (which formalizes the audit trail `db.py` reserves). | Keeps the transport free of a DB session; log event is the observable AC1 artifact. |
| D5 | Allowlist loaded from a **file path in env `REVALID_ALLOWLIST`**, falling back to a **built-in lab default** (`http://localhost:3000/*`). Never from report content. | No config infra yet; smallest trusted-config surface. |

## 3. Components

One new module, `src/revalid/allowlist.py`, following the flat-module convention
(`app`/`db`/`domain`/`ingest`). Four small, independently-testable units:

| Unit | Signature | Responsibility |
|---|---|---|
| `canonicalize` | `canonicalize(url: str) -> str` | Parse + normalize a URL to one canonical string. Pure. |
| `TargetGuard` | frozen dataclass; `patterns: frozenset[str]` | `is_allowed(url) -> bool`; `check(url) -> None` (audit + raise on miss). Immutable — no mutator. |
| `AllowlistTransport` | `httpx.BaseTransport` subclass wrapping `inner` + `guard` | `handle_request` runs `guard.check` before delegating. |
| `load_allowlist` | `load_allowlist(path: str | None = None) -> TargetGuard` | Parse a trusted config file (or env/default) into a guard. |

Plus `TargetNotAllowedError(Exception)` carrying `.target: str` and `.reason: str`,
and `DEFAULT_ALLOWLIST: frozenset[str] = frozenset({"http://localhost:3000/*"})`.

The pure matcher stays internal; the FR-05/FR-08 reuse surface is **not** built now
(YAGNI until those FRs exist).

## 4. Matching semantics

### 4.1 Canonicalization (`canonicalize`)

Applied identically to both the request URL and every allowlist pattern, so
matching compares like with like. Steps, using `urllib.parse.urlsplit`:

1. **Scheme** → lowercased. Missing scheme ⇒ invalid (see §7).
2. **Host** → `urlsplit(...).hostname`, lowercased. Using `.hostname` (not the raw
   authority) defeats the userinfo trick: `http://localhost:3000@evil/` parses to
   host `evil`, so it can never match a `localhost` pattern.
3. **Port** → appended as `:<port>` only if explicitly present. Default ports are
   **not** synthesized (an `http://…:3000` pattern will not match a port-less URL).
4. **Path** → percent-encoded `%2e`/`%2f` (case-insensitive) are **decoded first**,
   then dot-segments resolved with `posixpath.normpath` (`/rest/../admin` **and**
   `/rest/%2e%2e/admin` → `/admin`), so encoded traversal can't escape a narrowed
   subtree that literal traversal is denied from (security review, 2026-07-01);
   empty path becomes `/`; a trailing slash is preserved iff the original had one
   and the path is not root (so `…/` ≠ `…/rest`).
5. **Query** → preserved as `?<query>` (a trailing `*` naturally covers it).
   **Fragment** → dropped.

Canonical form: `f"{scheme}://{host}{port}{path}{query}"`.

### 4.2 Glob → regex (only `*` is special)

`fnmatch` is **not** used: it also treats `?` and `[...]` as metacharacters, which
appear in real URLs (queries, encodings) and would cause surprising matches. Instead
each pattern is compiled to an anchored regex where **only `*` is a wildcard**:

```
regex = re.escape(canonical_pattern).replace(r"\*", ".*")
is_allowed = any(re.fullmatch(regex_i, canonical_target) for regex_i in guard._regexes)
```

`re.escape` makes every other character literal; `\*` → `.*` matches any run of
characters including `/`. Regexes are compiled **once** at guard construction
(`functools.cached_property` over the frozen `patterns`), not per request.

### 4.3 Worked examples (become unit tests)

| Pattern | Target | Match? |
|---|---|---|
| `http://localhost:3000/` | `http://localhost:3000/` | ✅ exact |
| `http://localhost:3000/` | `http://localhost:3000/public` | ❌ (no `*`) |
| `http://localhost:3000/*` | `http://localhost:3000/public/deep` | ✅ subtree |
| `http://localhost:3000/rest/*` | `http://localhost:3000/rest/user?q=1` | ✅ (query covered) |
| `http://localhost:3000/*` | `http://localhost:3001/` | ❌ (port) |
| `http://localhost:3000/*` | `https://localhost:3000/` | ❌ (scheme) |
| `http://localhost:3000/*` | `http://localhost:3000@evil/x` | ❌ (host is `evil`) |
| `http://localhost:3000/rest/*` | `http://localhost:3000/rest/../../etc` | ❌ (normalizes to `/etc`, escapes `/rest/`) |

## 5. Enforcement flow

```mermaid
sequenceDiagram
    participant Ex as Executor (FR-07)
    participant T as AllowlistTransport
    participant G as TargetGuard
    participant Net as inner transport
    Ex->>T: handle_request(request)
    T->>G: check(str(request.url))
    alt allowed
        G-->>T: None
        T->>Net: handle_request(request)
        Net-->>Ex: response (3xx captured, not followed)
    else denied
        G->>G: audit "target_denied"
        G-->>T: raise TargetNotAllowed
        T-->>Ex: exception (no socket opened)
    end
```

- The executor builds `httpx.Client(transport=AllowlistTransport(inner, guard),
  follow_redirects=False)`. `follow_redirects=False` means a 3xx is captured as
  evidence, never chased — no redirect-hop bypass.
- `check()` is **fail-closed**: default is deny; the raise happens *before* the
  inner transport opens a socket.
- Because httpx routes every request (and, if ever enabled, every redirect) through
  the transport, there is no code path to the network that skips the guard.

## 6. "Never expanded from report content" (AC2), structurally

- `TargetGuard` is a **frozen** dataclass; `patterns` is a **`frozenset`**. There is
  no mutator — nothing at runtime (least of all `ingest`) can add a pattern.
- The guard is built **only** by `load_allowlist`, from env/file/default — all
  trusted config. There is no function anywhere that derives a pattern from a
  `Finding` or report field.
- AC2's test therefore proves an **invariant**, not a code path: feed a
  report-derived hostile URL, assert it is denied and the pattern set is unchanged.

## 7. Config, defaults & error handling

- **Source order**: explicit `path` arg → `REVALID_ALLOWLIST` env var → built-in
  `DEFAULT_ALLOWLIST`.
- **File format**: one glob per line; blank lines and `#` comments ignored;
  surrounding whitespace stripped.
- **Validation (fail-closed on bad config)**: every pattern must canonicalize to a
  URL *with a scheme and host*. A schemeless/hostless pattern raises
  `ValueError` at load time — a misconfigured allowlist must not silently allow or
  deny the wrong things.
- **Denial audit event**: `logging.getLogger("revalid.allowlist").warning(...)` with
  a stable event name `target_denied` and fields `{target, reason}`. Structured so
  FR-10 can later route the same event to the persistent audit trail.
- **Complexity**: each function stays ≤ xenon C by construction; `canonicalize`
  is the most involved and is decomposed if it trips the gate.

## 8. Testing (maps to acceptance criteria)

All tests are no-I/O ⇒ `tests/unit/test_allowlist.py` (httpx via `MockTransport`):

- **Matcher truth-table** — every row of §4.3, plus port/scheme sensitivity, dot-
  segment normalization, userinfo defeat, query coverage, and the `/` vs `/*` cases.
- **`load_allowlist`** — parses comments/blank lines; env-var and default fallback;
  **rejects a schemeless pattern** with `ValueError`.
- **Guard immutability** — `patterns` is a `frozenset`; `TargetGuard` has no setter;
  attempting mutation fails.
- **Transport, allowed** — `AllowlistTransport` over an `httpx.MockTransport`
  returning 200 → request to an allowed URL delegates and returns 200.
- **Transport, denied (AC1)** — request to a non-allowlisted URL raises
  `TargetNotAllowedError` **and** logs the `target_denied` audit event (`caplog`); the
  inner transport is never called.
- **SSRF guard (AC1 + AC2)** — build a guard from `DEFAULT_ALLOWLIST`; take a
  `Finding` whose `raw`/`affected_endpoints` carry `http://evil.example/`; assert
  `is_allowed` is `False`, a request raises + audits, and the pattern set is
  unchanged (report content cannot expand it).

Quality gates: coverage ≥ 80 % on the module, `mypy --strict`, `ruff`, xenon ≤ C.

## 9. Out of scope / deferred

- **Async transport** (`AsyncBaseTransport`) — add when an async executor needs it.
- **DB-persisted audit rows** — FR-10 (this ships the log event only).
- **DNS-rebinding / IP-literal equivalence** — deferred per D3; would be a
  resolve-and-check-IP layer if the threat model expands.
- **Double-encoded traversal** (`%252e%252e`) — single-level `%2e`/`%2f` decoding is
  applied (security review); a target server that *doubly* percent-decodes could still
  re-expose traversal. Out of scope while the default allowlist is whole-host (`/*`);
  revisit if narrowed path subtrees become load-bearing.
- **Matcher reuse at plan-approval (FR-05) / sanity-check (FR-08)** — the matcher is
  written reusably but no public reuse surface is built now.
- **`lab/docker-compose.yml`** — a separate M1 item; FR-06's default simply names the
  lab origin it will expose.

## 10. Acceptance-criteria traceability

| Criterion | Satisfied by |
|---|---|
| AC1 — non-allowlisted host fails closed + audit entry | §5 fail-closed raise + §7 audit event; test §8 "Transport, denied" |
| AC2 — report URLs never expand the allowlist | §6 frozen guard + config-only loading; test §8 "SSRF guard" |
| NFR-03 — enforced at executor level, non-destructive | §5 transport seam, `follow_redirects=False` |
