# FR-17 Slice 6a — Agentic Verdict Integration + Human Adjudication (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every code task (test first, watch it fail, implement, watch it pass). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the FR-17 agentic verdict into FR-09 (the `verdicts` table), FR-10 (audit re-derivation), and FR-12 (export), and let the human adjudicate it (accept / override). Purely additive — the batch path is untouched (Slice 6b retires it).

**Architecture:** Polymorphic storage — the frozen domain `Verdict`/`Evidence` is left alone; `VerdictRecord` gains a `source` discriminator (`batch`/`agentic`), a nullable `session_id` FK, and a nullable `evidence` column. The agent's verdict auto-persists in `record_verdict` (the single conclude/give-up hook, `actor="agent"`); human adjudication appends a superseding operator record (`actor="operator"`) + a `verdict_adjudicated` transcript event. FR-10 audit branches on `source` (batch → re-derive from evidence; agentic → transcript integrity). FR-12 `VerdictExport` flattens (embedded `Verdict` → flat fields + `source`/`session_id`/optional `evidence`); `SCHEMA_VERSION` 1.1 → 1.2. See `docs/superpowers/specs/2026-07-18-agentic-retest-console-slice-6a-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (SQLite, `create_all` — no Alembic), Pydantic AI (TestModel/FunctionModel in tests), pytest; React/TS/Vite/Tailwind, TanStack Query, vitest.

## Global Constraints

- Python 3.12+, managed with `uv`; run tools via `uv run` / `make`.
- `mypy --strict` must pass; ruff lint + format (line length 100, Google docstrings on public API).
- Complexity gate: xenon max absolute **C**; refactor, never suppress.
- Coverage ≥ 80% on `src/`; new pure/logic lines aim for 100%.
- Tests per pyramid level: `tests/unit/` (no I/O, LLM via TestModel/FunctionModel), `tests/integration/` (marker `integration`, real REST + `FakeSandbox`).
- Conventional Commits; every commit carries `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Frontend gates: eslint + `tsc` + `vite build` + vitest all green; `RetestSession`-owned pure logic stays pinned per the two-tier coverage floor.
- The sandbox egress lock (NFR-03), single-user model (ADR-0008), and the frozen domain `Verdict`/`Evidence` are **untouched**.
- Branch: `feat/fr17-verdict-adjudication-slice6a`. PR body must contain `Closes #102`.

---

## File Structure

**Backend:**
- `src/revalid/domain.py` — add `SessionEventKind.VERDICT_ADJUDICATED`.
- `src/revalid/db.py` — `VerdictRecord`: add `source`, nullable `session_id` FK, make `evidence` nullable; add `agentic()` classmethod constructor.
- `src/revalid/retest_session.py` — `record_verdict` auto-persists an agentic `VerdictRecord` (`actor="agent"`); add `adjudicate_verdict(session, session_id, status, rationale)` (append `verdict_adjudicated` event + superseding operator record + update session row).
- `src/revalid/audit.py` — `rederive_run` branches on `source`; add the agentic transcript-integrity check.
- `src/revalid/export.py` — flatten `VerdictExport`; `_verdict_export`/`_metrics` read flat fields; bump `SCHEMA_VERSION` 1.1 → 1.2.
- `src/revalid/eval.py` — read `verdict.status`/`verdict.evidence` directly (2 call sites).
- `src/revalid/app.py` — `AdjudicateRequest` model; `POST /api/retest-sessions/{id}/adjudicate` route + worker; `RetestSessionOut` already carries verdict fields (verify).

**Frontend:**
- `frontend/src/api/client.ts` — add `adjudicateSession(id, {status, rationale})`; extend types if needed.
- `frontend/src/routes/RetestSession.tsx` — adjudication panel (Accept / Override + rationale) on a terminal session.
- `frontend/src/routes/RetestSession.test.tsx` — panel tests.

**Docs / schema:**
- `docs/reference/schemas/run-export.schema.json` — regenerate (`make export-schema`).
- `docs/adr/0030-agentic-verdict-integration.md` (proposed) + `docs/adr/README.md`.
- `docs/requirements/srs.md` — FR-17 acceptance criteria for 6a.
- `docs/roadmap.md` — Slice 6a entry + tick.

---

## Tasks

### Task 1 — `VerdictRecord` polymorphic columns + `agentic()` constructor
- [ ] **Test** (`tests/unit/test_db.py` or nearest): `VerdictRecord.agentic(...)` builds a row with `source="agentic"`, `session_id` set, `evidence=None`, `actor`, `reason_code`; `from_domain` still yields `source="batch"` with evidence; a batch row's `to_domain()` round-trips unchanged.
- [ ] **Implement** in `db.py`: add `source` (`String(16)`, default `"batch"`), `session_id` (`ForeignKey("retest_sessions.id")`, nullable, default `None`), make `evidence` `Mapped[dict[str, Any] | None]` nullable; `from_domain` sets `source="batch"` explicitly; add the `agentic()` classmethod.
- [ ] **Verify:** unit test green; `mypy --strict`; ruff.

### Task 2 — Auto-persist the agent verdict in `record_verdict`
- [ ] **Test** (`tests/unit/test_retest_session.py`): after `record_verdict(...)` a query on `VerdictRecord` returns one agentic row (`actor="agent"`, `source="agentic"`, matching `finding_id`/`status`/`rationale`). Add a second test proving `_give_up` (budget exhaustion) also persists an inconclusive agentic row.
- [ ] **Implement:** in `record_verdict`, after stamping the session row, insert `VerdictRecord.agentic(finding_id=record.finding_id, session_id=session_id, status=status, rationale=rationale, actor="agent", reason_code="agentic_conclusion")` and commit.
- [ ] **Verify:** unit green; existing `record_verdict`/give-up tests still green; xenon (keep `record_verdict` ≤ C — extract a small helper if needed).

### Task 3 — `adjudicate_verdict` (append event + superseding operator record)
- [ ] **Test:** on a concluded session, `adjudicate_verdict(session, sid, FIXED, "human override")` appends a `verdict_adjudicated` event and inserts a second `VerdictRecord` (`actor="operator"`, `reason_code="operator_adjudication"`, higher id); the agent's row is unchanged (append-only). A no-op / guarded behaviour when the session isn't terminal or has no verdict (choose: guard returns without writing — test it).
- [ ] **Implement:** add `SessionEventKind.VERDICT_ADJUDICATED` to `domain.py`; add `adjudicate_verdict` to `retest_session.py` (append event, insert operator record, update session `verdict_status`/`verdict_rationale`). Pure DB — does not touch the registry.
- [ ] **Verify:** unit green; mypy; ruff.

### Task 4 — FR-10 audit agentic branch
- [ ] **Test** (`tests/unit/test_audit.py`): a run with one agentic verdict re-derives clean (`ok`); tampering the agentic row's `status` away from its transcript `verdict` event yields a `Discrepancy`; an operator row is checked against the latest `verdict_adjudicated` event; batch rows behave exactly as today (existing tests stay green).
- [ ] **Implement:** `rederive_run` branches on `record.source`; add `_rederive_agentic(session, record)` that loads the session's events, picks the authoritative event by actor, and diffs `(status, rationale)`. Keep `rederive_verdict` (batch) unchanged.
- [ ] **Verify:** unit green; xenon (extract helpers to stay ≤ C); mypy.

### Task 5 — FR-12 export flatten + schema bump
- [ ] **Test** (`tests/unit/test_export.py`): `VerdictExport` carries `source`/`session_id`/`status`/`reason_code`/`rationale`/`matched_indicators`/optional `evidence`; a batch verdict and an agentic verdict both export; `_metrics` sums timing over evidence-backed verdicts only and counts agentic in `verdicts_by_status`; the published schema matches (drift test) at `SCHEMA_VERSION == "1.2"`.
- [ ] **Implement:** flatten `VerdictExport`; rewrite `_verdict_export` to read flat columns (no `to_domain()`); update `_metrics`; bump `SCHEMA_VERSION`; update `eval.py`'s two `verdict.verdict.*` sites to `verdict.*`; regenerate the schema with `make export-schema`; update the drift test / any `_verdict` test builders in `test_eval.py`, `test_export.py`, `scripts/demo/evaluate_run.py`.
- [ ] **Verify:** `uv run pytest tests/unit/test_export.py tests/unit/test_eval.py` green; `make export-schema` leaves no diff; mypy; ruff.

### Task 6 — REST: `POST /api/retest-sessions/{id}/adjudicate`
- [ ] **Test** (`tests/integration/test_retest_session_api.py` or nearest): full flow over the app with a `FakeSandbox` + Pydantic-AI stand-in — start → conclude → `GET /api/verdicts` shows the agentic verdict → `POST …/adjudicate {status, rationale}` → 202/200 → the export's latest verdict for that finding is the operator's; a bad body → 422; unknown/non-terminal session → a clean no-op status.
- [ ] **Implement:** `AdjudicateRequest` model; register the route (extend the existing session-route registrar); a worker calling `adjudicate_verdict`. Keep route functions ≤ C.
- [ ] **Verify:** integration green; live-app smoke (uvicorn) that the route is wired.

### Task 7 — Frontend adjudication panel
- [ ] **Test** (`RetestSession.test.tsx`): on a terminal session the panel renders the agent's verdict + Accept / Override; Accept calls `adjudicateSession` with the agent's status; Override with the picked status + rationale; the panel is absent on a live (non-terminal) session.
- [ ] **Implement:** `adjudicateSession` in `client.ts`; the panel in `RetestSession.tsx` (mutation invalidates session + events queries). Keep the owned pure logic pinned per the coverage floor.
- [ ] **Verify:** eslint + tsc + vite build + vitest green.

### Task 8 — Docs, ADR, SRS, roadmap + full verification
- [ ] ADR-0030 (proposed) via the `adr` skill; add to `docs/adr/README.md`.
- [ ] SRS FR-17 acceptance criteria for 6a via the `requirements` skill.
- [ ] Roadmap entry + tick Slice 6a in the M6 list.
- [ ] **Full gate:** `uv run pytest` (unit + integration) green, coverage ≥ 80% on `src/`; `mypy --strict`; ruff; xenon C; frontend gates; `make demo-retest-session` still green.
- [ ] Commit per task (Conventional Commits + Co-Authored-By); open the PR with `Closes #102` + a filled "How to validate"; queue auto-merge.
