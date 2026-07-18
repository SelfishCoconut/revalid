# FR-17 Slice 6b-iii — Retire the batch path + reshape the finding flow (design)

- **Epic:** #87 · **ADR:** 0033 (proposed) · **Milestone:** M6 (closes FR-17)
- **Depends on:** 6b-ii (user-owned goal). **The last FR-17 slice.**
- **Date:** 2026-07-19

## 1. Problem

The agentic console is feature-complete (Slices 0–6b-ii), but the old FR-04/05/07-09
**batch execution** path still coexists: a user can still generate a plan of HTTP
probes, approve it, and run it mechanically. ADR-0025 always intended the agentic
console to *supersede* that path; keeping both is two ways to do one thing, extra
maintenance, and a muddier thesis story. Álvaro's decision (2026-07-19): **full
deletion — the clean single-path end-state.** The batch *execution* goes; FR-04 is
already repurposed as the goal generator (6b-ii), so it stays.

## 2. Decision

Retire the batch execution path entirely and reshape the finding UI around the
agentic console. Split into two PRs:

- **6b-iii-a — backend batch retirement** (delete the engine + endpoints + domain types).
- **6b-iii-b — SPA finding-flow reshape** (collapse the wizard to Extract → Goal → Agentic retest → Verdict).

FR-04/05/07/08/14 are re-marked **superseded by FR-17** in the SRS (implemented,
then superseded — the thesis narrative). NFR-02 for verdicts is now solely the
agentic transcript-integrity audit (ADR-0025/0030).

### 2.1 6b-iii-a — backend

**Delete outright** (modules + their unit tests):
- `approval.py` (FR-05 execute-approved-plan gate) + `test_approval.py`, `test_approval_execute.py`
- `retest.py` (FR-07 probe executor + technique registry + `assess_evidence`) + `test_retest.py`, `test_retest_api.py`, `tests/integration/test_retest_pipeline.py`
- `sanity.py` (FR-08 execution sanity checker) + `test_sanity.py`
- `browser.py` (FR-14 Playwright probe) + `test_browser.py`
- the `browser` optional extra (and its system test)

**Strip to goal-only:**
- `plan.py` — keep `GeneratedGoal`, `build_goal_agent`, `generate_goal`, `_finding_prompt`; remove `generate_plan`, `build_plan_agent`, `PlannedAction`, `RejectedAction`, `PlanResult`, `gate_actions`, `_gate`, `_empty_plan`, and the `revalid.retest` import (`GENERIC_KIND`, `classify_*`). `_finding_prompt` drops its now-unused `instructions` param. `test_plan.py` keeps only the `generate_goal` tests.
- `audit.py` — keep `_rederive_agentic`/`_transcript_verdict`; remove `_rederive_batch`, `rederive_verdict`, the `revalid.retest`/`revalid.sanity` imports, and the `source` branch in `rederive_run` (agentic-only now). `test_audit.py` keeps only the agentic cases.

**Collapse the domain/db (the batch types become dead):**
- `domain.py` — remove `Probe`, `RetestPlan`, `PlanStatus`, and the HTTP `Verdict` + `Evidence` (all batch-only after the deletions). Keep `AgenticEvidence`, `Finding`/`FindingOrigin`/`FindingStage` (FR-16), `Severity`, `Settings`, the FR-17 session enums, `VerdictStatus`.
- `db.py` — remove `PlanRecord`; on `VerdictRecord` remove `from_domain`/`to_domain`, `plan_id`, `plan_version`, and the `source`/`probe_kind` discriminators (every verdict is agentic). Keep `agentic()`, `id`, `finding_id`, `session_id`, `status`, `reason_code`, `rationale`, `matched_indicators`, `evidence` (an `AgenticEvidence` dict), `actor`, `created_at`.
- `export.py` — `VerdictExport.evidence: AgenticEvidence | None` (drop the HTTP `Evidence` union member + the `source` branch in `_evidence_export`); remove `PlanExport`/`_plan_export` (no plans). `SCHEMA_VERSION` **1.3 → 1.4**; regenerate + drift-test.
- `app.py` — `VerdictOut.evidence: AgenticEvidence | None`; remove `PlanOut`, `_edit_plan`, `_retest_finding`, the batch DI (`get_plan_agent`/`PlanAgentDep`, `get_probe_client`/`ProbeClientDep`, `get_browser_runner`/`BrowserRunnerDep`), and the 8 batch endpoints (`POST /findings/{id}/plan`, `/plan/approve`, `/plan/reject`, `/plan/revise`, `GET /plans`, `POST /retest`). Keep `GET /verdicts`, `GET /audit` (agentic-only), `GET /export`, and the whole FR-17 session surface.
- `eval.py` — unchanged shape; it reads `verdict.status`/`verdict.evidence.elapsed_ms`, both present on the agentic shape.

**Guiding principle:** dead code is removed as it surfaces (mypy `--strict` + vulture + the `codebase-sanity` audit are the check). A batch symbol that survives is a bug.

### 2.2 6b-iii-b — SPA finding-flow reshape

Collapse the 5-stage wizard (Extract → Plan → Approve → Retest → Verdict) to
**Extract → Goal → Agentic retest → Verdict**:
- Remove `PlanStage`, `ApproveStage`, `RetestStage`, and the batch `VerdictStage`
  (the batch verdict cards / `EvidenceView` HTTP branch); keep `ExtractStage`
  (FR-16 finding view/edit/notes).
- The **Goal** stage generates + edits the goal and launches the agentic session;
  the **Agentic retest** stage is the console (`RetestSession`), reachable as the
  finding's primary retest action rather than a buried button.
- `PipelineTrack` / `FindingLayout` / routing / `useFindingStage` reshape to the
  four stages; the batch `useRetest`/`usePlans` hooks + `retest`/`listPlans`/
  `generatePlan`/`approvePlan` client fns go.

### 3. Open question (6b-iii-b only) — the Goal stage

Two ways the Goal stage can work, to settle when we design 6b-iii-b:
- **(a) Generate-at-start (minimal):** the Goal stage is a launch point — "Retest
  with agent" creates the session, which generates the goal; the operator then
  edits it *in the console* (6b-ii already supports this). No new API.
- **(b) Pre-start draft:** the Goal stage generates + edits a draft goal *before*
  the session exists (your "edit the goal before it starts"), then "Start" passes
  it to session creation. Needs the start API to accept an initial goal + a draft
  store. Richer, more work.

This does not affect 6b-iii-a; we decide it when scoping 6b-iii-b.

## 4. Acceptance criteria (→ SRS FR-17, closes the umbrella)

1. The batch execution path is gone: no `approval`/`retest`/`sanity`/`browser`
   modules, no batch REST endpoints, no batch SPA stages; the agentic console is
   the only way to retest a finding. `mypy --strict`, vulture, and `codebase-sanity`
   find no batch remnant.
2. The finding flow is Extract → Goal → Agentic retest → Verdict; FR-16 finding
   versioning/notes and the FR-15 eval both still work over agentic verdicts.
3. FR-04/05/07/08/14 are re-marked *superseded by FR-17* in the SRS; ADR-0025 can be
   ratified `accepted`; the M6 release (`v0.5.0`/`v1.0.0`, Álvaro's call) can close.

## 5. Test plan

- **unit/integration** — delete the batch suites; keep + green the agentic suites
  (session, goal, audit-agentic, export/eval with `AgenticEvidence`). Add nothing
  new for a pure deletion beyond fixing fixtures the removed types touched.
- **schema** — regenerate `run-export.schema.json` (1.4) + drift test.
- **frontend** — delete the batch stage tests; keep the agentic console + FindingLayout
  tests, updated to the four-stage flow.
- **demos** — remove `make demo-plan`/`demo-approval`/`demo-sanity`/`demo-audit`(batch)
  /`demo-browser-xss`/`demo-techniques`; keep `demo-retest-session`, `demo-export`,
  `demo-eval`.
- **release gate** — the `codebase-sanity` agent before the M6 release.
