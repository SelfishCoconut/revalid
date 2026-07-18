# FR-17 Slice 6b-iii-a — Retire the batch execution path (backend)

> **For agentic workers:** This is a large **deletion** slice, not feature work. There is no test-first cycle — instead, each task deletes a dependency group and then **runs the full gate to green** (`uv run pytest tests/unit tests/integration -q`, `uv run mypy`, `uv run ruff check src tests scripts`, `uv run vulture src`) before committing. A surviving batch symbol is a bug — vulture + mypy are the check.

**Goal:** Delete the FR-04/05/07-09 batch *execution* path — `approval.py`, `retest.py`, `sanity.py`, `browser.py`, the 8 batch REST endpoints, and the now-dead batch domain/db types — leaving the agentic console as the only retest path. FR-04 stays (repurposed as `generate_goal`).

**Architecture:** Delete root-to-leaf so each commit compiles: app.py batch surface first, then `audit.py` collapses to agentic-only, then the modules delete, then the dead domain/db types collapse, then `export.py`/`VerdictOut` drop the HTTP `Evidence` union member (schema 1.3 → 1.4).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest; the SPA reshape is 6b-iii-b.

## Global Constraints

- Python 3.12+, `uv`; `uv run mypy` (bare = CI), ruff (line 100), xenon max absolute **C**, vulture clean on `src`.
- Coverage ≥ 80% on `src/` (deletion raises the ratio; keep it green).
- Conventional Commits + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Egress lock (NFR-03), single-user model (ADR-0008), FR-16 finding versioning/notes, FR-15 eval, and the whole FR-17 session surface are **untouched**.
- Branch: `feat/fr17-retire-batch-slice6b-iii-a`. PR body: **`Part of #110`** (6b-iii-b closes it) — do NOT write `closes #110` anywhere.

## The green gate (run after every task)

```
uv run pytest tests/unit tests/integration -q
uv run mypy
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run vulture src
uv run xenon --max-absolute C src
```

---

## Task 1: Remove the batch REST surface from `app.py`

**Delete from `src/revalid/app.py`:**
- Imports: the whole `from revalid.approval import (...)` block, `from revalid.browser import ...`, `from revalid.retest import build_probe_client, lab_base_url`, `from revalid.sanity import PlanDeviationError`, and the batch names from the `revalid.plan` import (`PlannedAction`, `PlanResult`, `RejectedAction`, `build_plan_agent`, `generate_plan`) — keep `GeneratedGoal`, `build_goal_agent`, `generate_goal`.
- The `PlanOut` model; the DI `get_probe_client`/`get_browser_runner`/`get_plan_agent` + `ProbeClientDep`/`BrowserRunnerDep`/`PlanAgentDep`; the helpers `_edit_plan`/`_approve_plan`/`_reject_plan`/`_revise_plan`/`_list_plans`/`_retest_finding`; the `run_plan_generation` worker; the registrars `_register_plan_routes` + `_register_retest_routes` (the 8 endpoints) and their calls in `create_app`.
- Keep `_register_retest_routes`'s `GET /verdicts` and `GET /audit` — **move them** into a small kept registrar (e.g. fold into `_register_export_routes` or a new `_register_audit_routes`) since they stay (agentic-only). `VerdictOut` keeps its `Evidence | AgenticEvidence | None` union **for now** (Task 6 narrows it).

**Delete these test files** (they test the removed endpoints):
- `tests/unit/test_retest_api.py` — EXCEPT the `/verdicts` + `/audit` cases: move those into `tests/integration/test_retest_session_api.py` (or a small `test_verdicts_api.py`), rewritten to seed an **agentic** verdict (via a concluded session) rather than a batch one. If that's fiddly, assert `/verdicts` empty + `/audit ok` on a fresh DB.
- `tests/integration/test_approval_api.py`, `tests/integration/test_retest_pipeline.py`.

- [ ] Delete the symbols + imports above; wire the kept `/verdicts` + `/audit`.
- [ ] Delete/patch the test files above.
- [ ] **Green gate.** Fix every mypy/vulture breakage (unused imports, etc.).
- [ ] Commit: `refactor(api): remove the batch plan/approve/retest endpoints (FR-17 6b-iii)`

---

## Task 2: Collapse `audit.py` to agentic-only

**In `src/revalid/audit.py`:** remove `rederive_verdict`, `_rederive_batch`, the `from revalid.retest import assess_evidence` and `from revalid.sanity import review_verdict` imports, the `Evidence` import if now unused, and the `record.source == "agentic"` branch in `rederive_run` (call `_rederive_agentic` unconditionally — all verdicts are agentic). Update the module docstring.

**In `tests/unit/test_audit.py`:** delete the batch cases (`test_rederive_reproduces_*`, `test_rederive_flags_a_verdict_that_no_longer_matches`, `test_rederive_verdict_is_deterministic`, the `_run_and_store`/`_sqli_generated`/`_evidence` helpers); keep the agentic cases (`test_rederive_reproduces_an_agentic_verdict`, `_flags_a_tampered_*`, `_rationale_only_drift`, `_checks_operator_row_*`, `_is_empty_when_no_verdicts`).

- [ ] Apply the removals. **Green gate.** Commit: `refactor(audit): agentic-only re-derivation — drop batch verdict re-derivation (FR-17 6b-iii)`

---

## Task 3: Delete `approval.py` + `sanity.py`

After Tasks 1–2, `approval` is imported by nothing (app + retest gone/next); `sanity` by nothing (approval + audit + app gone).

- [ ] `git rm src/revalid/approval.py src/revalid/sanity.py tests/unit/test_approval.py tests/unit/test_approval_execute.py tests/unit/test_sanity.py`
- [ ] Remove `scripts/demo/approval_gate.py`, `scripts/demo/execution_sanity.py` + the `demo-approval`/`demo-sanity` Makefile targets (and their `.PHONY` entries).
- [ ] **Green gate** (vulture will flag any remaining `approval`/`sanity` reference). Commit: `refactor: delete approval.py + sanity.py (FR-05/FR-08 batch path) (FR-17 6b-iii)`

---

## Task 4: Strip `plan.py`; delete `retest.py` + `browser.py`

**In `src/revalid/plan.py`:** keep `GeneratedGoal`, `build_goal_agent`, `generate_goal`, `_finding_prompt` (drop its unused `instructions` param + the instructions branch). Remove `PlannedAction`, `RejectedAction`, `PlanResult`, `gate_actions`, `_gate`, `_empty_plan`, `generate_plan`, `build_plan_agent`, `_INSTRUCTIONS` (the plan one), and the imports that go dead: `from revalid.retest import GENERIC_KIND, classify_finding_kind, classify_probe_kind`, `from revalid.allowlist import TargetGuard, canonicalize`, `Probe`/`RetestPlan` from domain, `urljoin`, `Iterable`, `agent_model_name`, `UnexpectedModelBehavior` (keep — `generate_goal` uses it). Trim `tests/unit/test_plan.py` to only the `generate_goal` tests + their helpers.

**Then delete** (now unused: retest by app/audit/approval/plan all gone; browser by retest/approval/app all gone):
- [ ] `git rm src/revalid/retest.py src/revalid/browser.py tests/unit/test_retest.py tests/unit/test_techniques.py tests/unit/test_browser.py`
- [ ] Remove `scripts/demo/plan_retest.py`, `techniques.py`, `browser_xss.py`, `walking_skeleton.py` + the `demo-plan`/`demo-techniques`/`demo-browser-xss`/`demo-walking-skeleton` Makefile targets (+ `.PHONY`).
- [ ] In `pyproject.toml`: remove the `browser = ["playwright>=1.48"]` extra and the `playwright.*` mypy override; remove `tests/system/test_retest_system.py` + `test_browser_xss_system.py` if present.
- [ ] **Green gate.** Commit: `refactor: delete retest.py + browser.py (FR-07/FR-14 batch path); strip plan.py to goal-only (FR-17 6b-iii)`

---

## Task 5: Collapse the dead domain/db types

**In `src/revalid/domain.py`:** remove `Probe`, `RetestPlan`, `PlanStatus`, and the HTTP `Verdict` + `Evidence` (all batch-only now). Keep `AgenticEvidence`, `VerdictStatus`, `Finding`/`FindingOrigin`/`FindingStage`, `Severity`, `Settings`, `ReportStatus`, `RetestSessionStatus`, `SessionEventKind`.

**In `src/revalid/db.py`:** remove `PlanRecord`; on `VerdictRecord` remove `from_domain`, `to_domain`, `plan_id`, `plan_version`, `source`, `probe_kind` (keep `agentic()`, `id`, `finding_id`, `session_id`, `status`, `reason_code`, `rationale`, `matched_indicators`, `evidence`, `actor`, `created_at`). Update `agentic()` to drop the `reason_code` free-text if desired (keep it — it carries `agentic_conclusion`/`operator_adjudication`). Remove the `Evidence`/`Verdict`/`Probe`/`RetestPlan`/`PlanStatus` imports that go dead.

**In `tests/unit/test_db_plan.py`:** delete the `PlanRecord` tests + the `from_domain`/`to_domain` (batch) `VerdictRecord` tests; keep the `agentic()` tests (drop assertions on the removed `source`/`probe_kind` fields). Rename the file to `test_db_verdict.py` if it no longer covers plans.

- [ ] Apply. **Green gate** — mypy/vulture will surface every stale reference (in `retest_session.py`'s `agentic(...)` call, `eval.py`, etc.); fix each. Commit: `refactor(domain,db): remove the batch verdict/plan/probe types; VerdictRecord agentic-only (FR-17 6b-iii)`

---

## Task 6: Narrow the export + `VerdictOut` evidence to agentic; schema 1.4

**In `src/revalid/export.py`:** `VerdictExport.evidence: AgenticEvidence | None`; `_evidence_export` returns `AgenticEvidence(**record.evidence) if record.evidence else None` (drop the `source` branch + the HTTP `Evidence`); remove `PlanExport` + `_plan_export` + `plans` from `RunExport`/`build_export`/`_metrics` (no plans). Bump `SCHEMA_VERSION` **1.3 → 1.4**; `make export-schema`; update the drift test.

**In `src/revalid/app.py`:** `VerdictOut.evidence: AgenticEvidence | None`; `_verdict_out_evidence` drops the `source`/`Evidence` branch.

**In `tests/unit/test_export.py` / `test_eval.py`:** update the verdict fixtures to the agentic-only shape (no `source`/HTTP `Evidence`/plans); the schema-version assertion → `"1.4"`.

- [ ] Apply. **Green gate.** Commit: `refactor(export): agentic-only evidence; drop plans from the run export; schema 1.3 -> 1.4 (FR-17 6b-iii)`

---

## Task 7: Docs, ADR-0033, SRS, roadmap + full verification + PR

- [ ] `codebase-sanity` sweep on `src/` (no batch remnant) — run the `codebase-sanity` agent; address anything real.
- [ ] ADR-0033 (proposed) via the `adr` skill: decision = retire the batch execution path (full deletion), agentic console supersedes FR-04/05/07-09; note FR-14 dropped, schema 1.4, ADR-0025 now ratifiable. Add to `docs/adr/README.md`; mark superseded ADRs (0011/0012/0014/0015-batch/0018/0019 as applicable) accordingly.
- [ ] SRS: re-mark FR-04/FR-05/FR-07/FR-08/FR-14 **superseded by FR-17** (implemented → superseded); add the FR-17 6b-iii AC.
- [ ] Roadmap: 6b-iii-a entry; note 6b-iii-b (SPA) remains.
- [ ] **Full gate** (the green gate above) + `cd frontend && npx vitest run` (the SPA still builds against the API — 6b-iii-b reshapes it; ensure nothing the SPA calls was removed without a stub, or accept the frontend batch calls 404 until 6b-iii-b — the frontend build/tests are unaffected by backend deletion since they mock the client). `make demo-retest-session && make demo-export && make demo-eval` green.
- [ ] Push; open the PR "FR-17 Slice 6b-iii-a: retire the batch execution path (backend)" with a filled "How to validate"; body **`Part of #110`**; queue squash auto-merge; monitor CI to green.

---

## Self-Review (completed during authoring)

- **Spec coverage:** §2.1 every bullet maps to a task (modules → T3/T4, plan strip → T4, audit → T2, endpoints → T1, domain/db → T5, export/VerdictOut/schema → T6, eval → T6). §2.2 (SPA) is 6b-iii-b. §4 AC → T7.
- **Order safety:** root-to-leaf — app.py (T1) and audit (T2) drop the consumers before the modules delete (T3/T4); domain/db (T5) and export (T6) collapse after their consumers are gone; vulture + mypy gate each step.
- **No placeholders:** the deletions are enumerated by symbol/file; the one judgement call (where to re-home `/verdicts` + `/audit`) is called out in T1.
