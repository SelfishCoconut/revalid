# 0019. Extensible retest-technique registry: kind-keyed assessors + command rendering, with FR-04 kind tagging (scope stays human-validated)

Date: 2026-07-15
Status: proposed

## Context

The end-to-end run on the **real** Juice Shop report (2026-07-15) proved the
plumbing — 8/8 findings extract, plan, execute, export, and re-derive — but
every retest verdict came back `inconclusive`. Three forces converged to make
this the next decision:

1. **No assessor for planned probes.** `retest.assess_evidence(kind, evidence)`
   dispatches on `probe.kind`, and only `sqli-login-bypass` and `browser-xss`
   have assessors. FR-04 (ADR-0011) stamps *every* LLM-planned probe
   `kind="planned-http"`, which has no matcher → `assess_generic` →
   `no_assessor`/`inconclusive`. ADR-0011 foresaw exactly this ("verdict matching
   for generated actions is still hardcoded until FR-08/FR-09 generalize it …
   SQLi, XSS, IDOR, traversal …"). This ADR is that promised generalization.

2. **Weak-model plan loss.** On a 9B local model, 3/8 findings produced no
   runnable plan. Two causes: schema-retry exhaustion, and — dominant — the model
   copying the *report's original host* into targets, which the FR-06 gate
   correctly drops as `not_allowlisted`. A retest that hits the lab must target
   the lab, not the host printed in the source report.

3. **Product direction: human is the final arbiter** (2026-07-15). The machine
   verdict and (forthcoming) LLM summary are *advisory*; Álvaro
   confirms closed/open. So assessors must be **conservative** (never confidently
   wrong) more than they must be complete — a mis-read should land on
   `inconclusive`, never on a wrong confident verdict. Álvaro also asked that the
   retest catalog hold "the most common ones for web testing" and be something he
   can **add to and remove from** over time.

Constraints that cannot move: assessors stay **pure over `Evidence`** (FR-10 —
a verdict is a function of the stored `(kind, evidence)` pair, re-derivable with
no network); the FR-08 sanity guard still post-processes every verdict (it only
ever downgrades an over-confident `fixed`); and no arbitrary command execution
(ADR-0011/0012/0014 — the LLM proposes typed actions, deterministic code runs
them).

## Decision

Introduce a **retest-technique registry** as the single extension seam, seed it
with the common web-testing techniques we can execute safely today, tag probes
with a technique `kind` during FR-04 planning, and normalize planned-target
hosts onto the lab.

### 1. The technique registry (the extension point)

A **retest technique** is one thing keyed by a stable `kind` slug, bundling:

- an **assessor**: `Evidence -> Verdict`, pure (FR-10);
- a **command renderer**: `Probe -> list[str]`, the human-readable rendering
  shown for approval and quoted in the LLM summary (a later ADR);
- an **executor class**: `http` (FR-07, httpx) or `browser` (FR-14, Playwright)
  — the routing `is_browser_probe` already performs.

Adding a technique is **one registry entry**; removing it deletes that entry and
the kind falls back to the generic assessor (`no_assessor`/`inconclusive`) and a
generic curl rendering — never an error, never a guessed verdict. `assess_evidence`
stays the stable public entry point (imported by `audit.py` for re-derivation and
by the tests), so extending the registry never touches FR-09/FR-10 call sites.

### 2. Seed techniques (common web-testing classes, safely executable now)

Guiding principle — **conservative assessment**: a confident verdict
(`still_open`/`fixed`) requires an *unambiguous* HTTP signal; `fixed` only ever
rests on a **positive denial** (401/403), never on **absence** (404) — which is
exactly the FR-08 stance, so the assessor and the sanity guard never fight.
Everything ambiguous is `inconclusive`. This bounds NFR-01 (zero
confidently-wrong on ambiguity) even under a mis-assigned kind, because
mis-classification degrades to `inconclusive`, not to a wrong confident verdict.

| kind | web-testing classes | still_open | fixed | inconclusive |
|---|---|---|---|---|
| `sqli-login-bypass` *(exists)* | SQLi auth bypass | 200 + auth token | 401 | 404 / other |
| `browser-xss` *(exists)* | DOM / stored XSS (browser) | dialog fired | not reflected & not executed | reflected-not-executed |
| **`access-control`** *(new)* | IDOR / BOLA / basket read, missing-auth, admin-section access, broken access control | 200 with a non-empty body (resource served) → `unauthorized_access_succeeded` | 401 **or** 403 (positive denial) → `access_denied` | 404/410 → `endpoint_changed`; 3xx → `ambiguous_redirect`; else → `unexpected_response` |
| **`sensitive-file-exposure`** *(new)* | directory traversal, backup/config-file exposure | 200 with a non-empty body (file readable) → `sensitive_file_readable` | 401 or 403 (positive denial / traversal filter) → `access_denied` | 404/410 → `endpoint_changed`; else → `unexpected_response` |
| `planned-http` *(generic, exists)* | unclassified | — | — | always → `no_assessor` |

- IDOR/BOLA, missing-auth, and admin access are, *from HTTP evidence*, one
  decision — was a should-be-denied request served or denied? — so they share the
  single `access-control` assessor (no per-class duplication, the #1
  AI-development failure mode). The finding's title carries the class for
  reporting; the kind carries the matcher.
- **XSS stays browser-only.** DOM/stored XSS is verified in a real browser
  (`browser-xss`, FR-14). An HTTP "reflected-XSS" assessor cannot reliably
  separate reflection from execution without a fixed marker the LLM won't emit,
  and would risk confident-wrong verdicts — so it is deliberately not added.
- **Admin *hash-routes* remain out of HTTP scope** (ADR-0011's `#`-fragment
  limitation): `access-control` targets the *API endpoint* behind an admin view
  (e.g. `GET /api/Users`), not the client-side `/#/administration` route, which
  only a browser probe can exercise.

### 3. Command rendering

Each technique renders its probe in the **idiom of its executor**: HTTP probes as
a faithful, copy-pasteable **`curl`** line (a 1:1 rendering of the exact request
we send — nothing added or hidden, so approving the command approves the bytes on
the wire and Álvaro can reproduce a retest by hand); browser probes as **ordered
browser steps** (navigate → observe). The renderer is per-technique, so a new
technique ships its own idiom. Rendering is display-only — it never changes what
executes.

### 4. Probe-kind assignment in FR-04 (`plan.py`)

`PlannedAction` gains a **lenient** `kind: str = ""` the model fills from the
taxonomy (guidance added to the instructions). Lenient — a plain string, not a
schema-enforced enum — **on purpose**: a required enum would add schema-retry
failures on weak models, the opposite of the robustness force (2). The
deterministic gate is authoritative: `classify_probe_kind(proposed, fallback)`
normalizes the model's hint to a canonical registered kind (synonyms mapped;
unknown → `planned-http`), and when the model omits a kind a keyword classifier
over the *finding* (title/description/endpoints) supplies the fallback. The gate
stamps the resolved kind onto the `Probe`, replacing the hardcoded `planned-http`.
Re-derivation is unaffected — the chosen kind is persisted on the verdict record
and re-dispatched from storage (FR-10).

### 5. Targets resolve against a human-validated scope (no silent rehosting)

Planned targets keep resolving against the configured `base_url` and are gated by
the FR-06 `TargetGuard` exactly as today — **no automatic host rewriting**. The
"3/8 findings dropped" symptom seen on the home lab is an **evaluation artifact**:
the source report names the lab's stand-in host rather than the deployed target.
In normal use the retest hits the *same online endpoints the report documents*, so
the model copying the report's host is *correct*, and silently rewriting it would
be wrong (Álvaro's call). The general fix is therefore **human scope validation**,
not a heuristic: the authorized scope (base URL + allowlist) and the resolved and
dropped targets are surfaced for the user to confirm or correct before execution —
designed in a later human-in-the-loop ADR (scope validation). FR-04 keeps only the
low-risk robustness aids that are unambiguously safe — raised output retries and
instructions to prefer relative paths — but the host decision belongs to the
human, never to a silent gate rewrite.

## Alternatives considered

- **Two hardcoded assessors, no registry.** Rejected: Álvaro explicitly wants to
  add/remove techniques over time; a registry makes that one entry instead of
  edits scattered across dispatch, rendering, and routing.
- **Arbitrary tool execution (LLM runs `sqlmap`/`nikto`/a shell).** Rejected: it
  discards the FR-06 allowlist, FR-08 sanity guard, and FR-10 re-derivability, and
  is a far weaker security story for the tribunal. The registry is the seam to add
  a *constrained, gated* tool-executor later — each as its own ADR with its own
  sandbox and assessor — not a blanket shell.
- **LLM classifies kind as a schema-enforced enum.** Rejected: raises schema-retry
  failures on weak models (force 2). A lenient hint + code normalization is more
  robust and keeps code authoritative.
- **Deterministic-only kind classification (no model hint).** Rejected as sole
  path: brittle across the open finding set; the model's reading of reproduction
  steps is genuinely useful. Kept as the *fallback*, not the only source.
- **Per-class assessors (idor/admin/missing-auth each its own function).**
  Rejected: from HTTP evidence they are one decision; one matcher, synonym-mapped
  kinds, avoids duplication.
- **`fixed` on 404 for file-exposure ("file gone = fixed").** Rejected: a 404
  cannot distinguish removal from relocation; FR-08 downgrades it anyway. Require a
  positive 403.
- **Automatic host normalization (rehost planned targets onto the lab base).**
  Rejected (Álvaro): the lab-host mismatch is an *evaluation artifact*; in
  production the report's hosts *are* the retest targets, so silently rewriting
  them would be wrong. Mis-targeting is corrected by **human scope validation**
  (a later ADR), which keeps the human the arbiter of scope and does not touch the
  FR-06 boundary.

## Consequences

- **Easier:** real-report retests now yield conclusive *advisory* verdicts for the
  common web classes; FR-15 produces a real number; the LLM summary (a later ADR)
  has something to summarize; weak-model plans survive the gate far more often;
  adding a web-testing technique is a one-entry change.
- **Harder / accepted:** a 200 on an `access-control` probe is read as
  still-open, so a *benign* public endpoint mis-targeted by the planner would be a
  false still-open — a **planning** error, surfaced to the human arbiter, not an
  assessor bug. The FR-06 boundary is **unchanged** (no rehosting); a target the
  gate drops for being off-scope is fixed by the human via scope validation
  (a later ADR), so weak-model host-copying is a review step, not a silent rewrite.
- **Re-derivability preserved:** assessors stay pure over `(kind, evidence)`; new
  reason codes are additive; existing stored verdicts re-derive unchanged (their
  kinds are untouched). `assess_evidence` remains the stable seam `audit.py` uses.
- **FR-08 interaction verified:** `fixed`-on-401/403 passes the sanity review
  untouched; `fixed` never rests on 404/3xx by construction, so the guard's
  downgrade stays a backstop, not a corrector.
- **Status `proposed`:** the registry design, the seed taxonomy, and the
  model-hints/code-normalizes kind assignment are Álvaro's to ratify in async
  review. The FR-06 boundary is intentionally left untouched — scope correction
  is human-validated (a later ADR).
