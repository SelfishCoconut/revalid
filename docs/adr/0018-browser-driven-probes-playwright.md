# 0018. Browser-driven probes via Playwright, as a swapped executor under the same guard

Date: 2026-07-15
Status: proposed

## Context

FR-14 (Could) asks the executor to verify findings that HTTP-level probes cannot:
a DOM/JS-dependent vulnerability such as a client-side XSS executes in the
browser and is invisible in the raw HTTP response. The constraint is that such a
probe must run under the **same** approval (FR-05), allowlist (FR-06), and audit
(FR-10) guarantees as any other — a browser must not become an escape hatch
around the gate. The acceptance criterion is one browser-only-verifiable
XSS-class Juice Shop finding receiving a correct verdict.

The execution core is well-shaped for this: FR-08's `guarded_run` already wraps
every probe with the plan-deviation block and ambiguity downgrade, and FR-10's
`assess_evidence` turns stored evidence into a verdict purely. What differs for a
browser probe is only *how the evidence is produced* (drive a browser vs. send
one HTTP request) — not the gate, not the assessment, not the audit.

## Decision

Add browser probes as a **swapped executor behind the unchanged guard**, with
Playwright as an optional dependency.

- **`src/revalid/browser.py`** — `stored_xss_probe(base_url)` builds a
  `browser-xss` probe against Juice Shop's client-side search sink;
  `run_browser_probe(probe, guard)` is a Playwright (sync) runner that
  allowlist-checks the navigation URL *before* launching, gates **every** in-page
  `http(s)` request through the FR-06 guard via `page.route` (so a page cannot
  fetch off-allowlist), observes execution (a dialog handler catches the payload
  firing; the DOM is checked for the payload marker), and serializes that
  observation into the probe's `Evidence` (status-0 on a navigation failure, so
  the assessor calls it inconclusive rather than raising).
- **Optional dependency.** Playwright is the `browser` extra and is imported
  *lazily* inside the runner, so the package and every HTTP-only path work
  without it; an approved browser probe run without the extra raises
  `BrowserProbeUnavailableError` → HTTP 501.
- **Same guard, swapped executor.** `guarded_run` is refactored to take a
  probe-executor callable, so the FR-08 block + downgrade apply identically to
  browser probes; `execute_approved_plan` dispatches by `is_browser_probe` and
  both paths converge on the shared `assess_evidence`. A new `browser-xss`
  assessor reads the stored observation, so a browser verdict **re-derives
  offline** like any other (FR-10). The browser runner is an injectable
  `BrowserRunner = Callable[[Probe], Evidence]` (mirroring the httpx client
  dependency), so unit tests use a canned runner and a real browser is exercised
  only by `tests/system/test_browser_xss_system.py`.

**Scope (honest deviation).** The exemplar is Juice Shop's **DOM-based** XSS —
the canonical *browser-only-verifiable* client-side XSS, reliably present in the
pinned lab. The AC says "stored-XSS-class"; DOM XSS is the same class of
browser-only-verifiable client-side execution, and a genuinely *persisted* XSS
would use the identical `browser-xss` probe kind and assessor with a different
URL/setup flow — no engine change. This is called out as a deliberate
simplification for a Could-priority feature. FR-04 does **not** generate browser
probes (they are hardcoded like the M1 SQLi probe); browser-probe *planning* is
out of scope.

## Alternatives considered

- **Playwright as a core dependency.** Rejected: it pulls a browser engine and is
  Could-priority; HTTP-only users should not pay for it. An optional extra +
  lazy import keeps the default install and CI unit job light.
- **Allowlist-check only the navigation URL.** Rejected as insufficient: a loaded
  page can `fetch` anywhere. Every in-page `http(s)` request is gated through the
  same `TargetGuard`; non-network schemes (`data:`/`blob:`) pass through.
- **A separate browser execution chokepoint.** Rejected: it would duplicate the
  safety-critical FR-08 wrapper. Making `guarded_run` executor-agnostic keeps a
  single guard for both transports (a browser probe cannot skip the block or the
  downgrade).
- **Detect XSS by parsing the HTTP response.** Rejected: that is exactly what
  fails for DOM/JS XSS — execution must be observed in a browser.

## Consequences

- **Easier:** DOM/JS-dependent findings become verifiable, inheriting the FR-05
  gate, FR-06 allowlist, and FR-10 re-derivability unchanged; a browser verdict
  is assessed and audited exactly like an HTTP one.
- **Harder / accepted debt:** a heavy optional dependency plus a browser binary
  to provision; the live-browser code (`run_browser_probe`, `_drive`) is covered
  only by the system test (unit tests use a canned runner, and those two
  functions are `pragma: no cover`); the exemplar is DOM — not persisted — XSS.
- `guarded_run`'s signature changed (now takes a probe executor); its callers and
  the FR-08 unit tests were updated, and `sanity.py` no longer imports httpx —
  it is now execution-agnostic, which is arguably cleaner.
