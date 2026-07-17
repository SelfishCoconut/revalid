# Software Requirements Specification (SRS)

> Source: requirements elicitation interview with the author, **2026-06-11**.
> Format: ISO/IEC/IEEE 29148-inspired catalogue. Each FR maps 1:1 to a GitHub issue
> (`req:FR-xx` label). Maintained with the `requirements` skill; changes require the
> author's approval and, for scope changes, an ADR.

## 1. Purpose & scope

`revalid` automates the revalidation (retesting) of findings reported in penetration-test
reports. It ingests a report, extracts each finding and its reproduction steps, derives an
executable retest plan, executes it **only against authorized lab targets** after human
approval, and produces an evidence-backed verdict per finding: **still-open / fixed /
inconclusive**.

**In scope (this TFG):** web-application vulnerabilities (XSS, SQLi, auth/access control,
misconfiguration) against local lab targets (OWASP Juice Shop, DVWA); PDF and structured
(JSON/XML) report ingestion; HTTP-level probes; a local single-user web application
(FastAPI + React SPA, localhost only).

**Out of scope (Won't, future work):** network/service and API-schema finding classes,
browser-DOM-dependent probes beyond FR-14, multi-user operation, authentication,
non-lab targets, destructive exploitation.

## 2. Functional requirements

### FR-01 — Ingest PDF pentest reports
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall accept a PDF pentest report and extract its content for finding identification, tolerating common report layouts (headings, tables, finding sections).
- **Acceptance criteria**:
  - [ ] Uploading the evaluation Juice Shop PDF report yields raw finding candidates without manual preprocessing.
  - [ ] A malformed/non-report PDF is rejected with a clear error, not a crash.

### FR-02 — Ingest structured reports (JSON/XML)
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall ingest machine-readable findings exports (initial target: DefectDojo-style JSON; one XML format) by schema mapping, without LLM involvement.
- **Acceptance criteria**:
  - [ ] A DefectDojo-format JSON export imports with all findings mapped to the internal model.
  - [ ] Unknown fields are preserved in a raw-payload attribute for audit.

### FR-03 — Extract structured findings
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall extract, per finding: title, description, severity, impact, attack vector, affected endpoint(s), and ordered reproduction steps, into a validated schema (Pydantic). Extraction from unstructured input uses the LLM; output failing validation is retried/flagged, never silently accepted.
- **Acceptance criteria**:
  - [ ] ≥ 90% of findings in the evaluation report are extracted with all mandatory fields present.
  - [ ] Invalid LLM output never reaches persistence (property: schema validation gate).

### FR-04 — Generate executable retest plans
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: For each finding, the system shall derive a retest plan: an ordered list of typed, non-destructive HTTP probe actions with expected still-open/fixed indicators, generated from the reproduction steps.
- **Acceptance criteria**:
  - [ ] Each plan action is a typed object (no free-form commands) referencing only allowlisted targets.
  - [ ] Each plan states, per action, the indicator that would mark the vulnerability present.

### FR-05 — Human plan review & approval
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The web UI shall present each retest plan for review; the user can approve, reject, or edit per finding (and batch-approve). No plan executes without approval.
- **Acceptance criteria**:
  - [x] Unapproved plans are not executable through any code path (enforced server-side, not only in UI).
  - [x] Plan edits are versioned; the executed version is recorded in the audit trail.

### FR-06 — Target authorization allowlist
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The executor shall refuse any action whose target is not on the configured allowlist (default: the lab compose targets). Allowlist changes are explicit configuration, never inferred from report content.
- **Acceptance criteria**:
  - [ ] An approved plan referencing a non-allowlisted host fails closed with an audit-trail entry.
  - [ ] Report-supplied URLs never expand the allowlist (SSRF guard test).

### FR-07 — HTTP probe executor
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall execute approved plans via HTTP (httpx), capturing full request/response evidence per step. Probes are verification-only: no destructive payloads, no state-damaging operations.
- **Acceptance criteria**:
  - [ ] Each executed step persists request, response (status/headers/body excerpt), timing, and matched indicators.
  - [ ] Known Juice Shop findings from the evaluation set are detectable end-to-end via HTTP probes.

### FR-08 — Execution sanity checker
- **Priority**: Must · **Source**: interview 2026-06-11 (author's design)
- **Description**: An independent verifier shall monitor execution against the approved plan and the finding's intent. It shall detect (a) deviation from the approved plan, and (b) ambiguous outcomes — e.g. the model rationalizing between "vulnerability patched" and "endpoint changed/moved" — forcing the verdict to *inconclusive* with a stated reason instead of a guess.
- **Acceptance criteria**:
  - [x] A plan-deviation test case (executor attempts an action not in the plan) is blocked and logged. *(ADR-0014: `sanity.assert_in_plan` fail-closed — logs + raises `PlanDeviationError` before any request; API maps it to 409.)*
  - [x] An endpoint-moved test case (finding's path returns 404 while the app is up) yields *inconclusive* with reason "endpoint changed", never *fixed*. *(ADR-0014: `sanity.review_verdict` downgrades any *fixed* on 404/410 → `endpoint_changed` and on 3xx → `ambiguous_response`; verified through `execute_approved_plan`.)*

### FR-09 — Evidence-backed verdicts
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall assign per finding: **still-open / fixed / inconclusive**, each linked to the evidence that justifies it (payload used, matched indicator, request/response excerpts).
- **Acceptance criteria**:
  - [ ] No verdict exists without linked evidence records.
  - [ ] Inconclusive verdicts always carry a machine-readable reason code.

### FR-10 — Full audit trail
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: Every system action (ingestion, extraction, plan generation, approval, each probe, verdict) shall be persisted with timestamp and actor (user / model / executor) such that any verdict can be re-derived from the trail alone.
- **Acceptance criteria**:
  - [x] For any completed run, a re-derivation routine reproduces every verdict from stored data only (no re-execution). *(ADR-0015: `audit.rederive_run` recomputes each verdict from its stored evidence via the shared pure `retest.assess_evidence` + FR-08 `review_verdict`; `GET /api/audit` + `make demo-audit`. `VerdictRecord` gained `created_at`/`actor` for the timestamp+actor trail.)*

### FR-11 — Results dashboard (web UI)
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The React SPA shall provide: report/run overview, finding list with verdicts, drill-down to evidence and audit trail, and the plan-approval workflow (FR-05). Served by FastAPI on localhost only.
- **Acceptance criteria**:
  - [x] The full evaluation flow (ingest → approve → execute → verdicts with evidence) is operable from the UI alone. *(ADR-0013: Vite/React/TS/Tailwind SPA served by FastAPI at `/`, API under `/api`; PDF upload runs FR-01→FR-03 as a background job the UI polls. Verified end-to-end in a real browser on a live Ollama backend — upload → 4 findings → plan → approve → retest → evidence-backed verdict — plus unit/integration coverage of the `/api` chain.)*

### FR-12 — Machine-readable results export
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall export a complete run (findings, plans, verdicts, evidence references, metrics) as a versioned JSON document; the evaluation harness consumes this format.
- **Acceptance criteria**:
  - [x] Export validates against a published JSON schema; the evaluation harness (FR-15) runs on it. — `src/revalid/export.py` (ADR-0016): `RunExport` (reports/findings/plans/verdicts+evidence/metrics), versioned by `SCHEMA_VERSION`; schema generated from the model to `docs/reference/schemas/run-export.schema.json` (`make export-schema`, drift-tested); `GET /api/export` + `/api/export/schema`; `make demo-export` validates a run against the published schema.

### FR-13 — Pluggable LLM backends (Claude primary, local fallback)
- **Priority**: Should · **Source**: interview 2026-06-11
- **Description**: The LLM layer (Pydantic AI) shall be model-agnostic: Claude API as primary; a local model (Ollama) configurable as fallback and as comparison condition in the evaluation.
- **Acceptance criteria**:
  - [ ] Switching backends is configuration-only (no code change); both run the extraction test suite.
  - [x] The active backend is a **user-editable, DB-persisted setting** changeable at runtime (env vars seed a fresh DB; the stored row is then authoritative). Model discovery + a connection test surface in the SPA `/settings` view. (ADR-0021)

### FR-14 — Browser-based probes (Playwright)
- **Priority**: Could · **Source**: interview 2026-06-11
- **Description**: For findings not verifiable at HTTP level (DOM/JS-dependent), the executor may support Playwright-driven browser probes under the same approval, allowlist, and audit constraints.
- **Acceptance criteria**:
  - [~] At least one stored-XSS-class Juice Shop finding verifiable only in-browser gets a correct verdict. — `src/revalid/browser.py` (ADR-0018): a `browser-xss` Playwright probe (optional `browser` extra) verifies Juice Shop's DOM XSS in a real browser under the same FR-05/FR-06/FR-10 constraints (`guarded_run` is executor-agnostic; browser verdicts re-derive via the shared `assess_evidence`). Pipeline + assessor unit-tested with a canned runner; the live-lab still-open verdict is asserted by `tests/system/test_browser_xss_system.py` (nightly `system-tests.yml`). Exemplar is DOM (browser-only-verifiable) XSS, not persisted — same probe kind/assessor generalizes.

### FR-15 — Evaluation harness
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall include a harness that runs the evaluation set (author's Juice Shop report vs a deliberately vulnerable instance) against ground truth and computes verdict-reliability metrics (per NFR-01) for the thesis Results chapter.
- **Acceptance criteria**:
  - [~] One command produces the metrics table (correct / wrong / inconclusive per finding, totals, timing) from a run export. — harness shipped: `src/revalid/eval.py` (ADR-0017), `make eval EXPORT=… GROUND_TRUTH=…` (exit-code gated on NFR-01), `make demo-eval` offline. Matches an FR-12 export to a title-keyed ground truth and buckets each finding conservatively (an over-cautious *inconclusive* is a safe miss, not *wrong*). Pending FR-15 completion: Álvaro's real ground truth + a live-lab run for the reported figure.

### FR-16 — Operator finding revision & annotation
- **Priority**: Should · **Source**: change request 2026-07-16 (ADR-0024)
- **Description**: The system shall let the operator amend and annotate an extracted finding without ever destroying history: (a) each edit records a new immutable finding **version** (extraction is version 1), symmetric with FR-05 plan versioning (ADR-0012) — the finding's stable **identity** is what plans and verdicts reference, so amendments never orphan them; and (b) the operator may attach **notes**, each timestamped and tagged with the pipeline stage it was written on, appended to a per-finding log.
- **Acceptance criteria** (met — PR #82, verified 2026-07-16):
  - [x] Editing a finding appends an immutable version (extraction = v1) and never mutates/deletes a prior version; `GET /api/findings/{id}/versions` returns the full ordered history.
  - [x] The finding views and `GET /api/findings` return the current version; existing plans/verdicts still resolve to the same finding after an edit (stable identity, no FK breakage).
  - [x] A note posted with a stage tag is appended to the finding's log and returned newest-first with its stage + timestamp; notes are append-only.
  - [x] The FR-12 export includes each finding's version history + notes; `SCHEMA_VERSION` is bumped (1.0 → 1.1) and the published schema regenerated + drift-tested.
- **Traces to**: issue #80, ADR-0024 (accepted); enhances FR-11 (wizard surface — deep-link redirect #84), FR-02/FR-03 (finding model), FR-12 (export).

### FR-17 — Interactive agentic retest console
- **Priority**: Must · **Source**: change request 2026-07-16 (ADR-0025, epic #87)
- **Description**: The system shall offer, per finding, an **interactive, sandboxed, human-in-the-loop agentic retest session** as the successor to the FR-04/05/07-09 batch-plan model: an LLM agent reasons, proposes a command, observes its output, and decides the next step — instead of executing a fixed plan generated up front — while a human approves every command before it runs. This is an **umbrella requirement**, built walking-skeleton-first across six slices (design spec: `docs/superpowers/specs/2026-07-16-agentic-retest-console-design.md`); acceptance criteria accumulate as each slice lands, mirroring FR-16.
- **Acceptance criteria — Slice 0** (met — issue #88, ADR-0025 proposed, 2026-07-16):
  - [x] **AC1**: from a finding, an operator starts a session; a sandboxed agent proposes one shell command + rationale, the operator approves it, the command runs in an egress-locked container, and the agent concludes with a verdict (still_open / fixed / inconclusive).
  - [x] **AC2**: no command executes before human approval — enforced structurally by the Pydantic AI deferred-tool gate (`run_command` cannot resolve without an explicit `ToolApproved`/`ToolDenied` resume), not by policy alone.
  - [x] **AC3**: the session transcript (`session_events`) is append-only and replayable — every proposed/approved/rejected command, its output, each state transition, and the final verdict, ordered by a monotonic sequence number.
  - [x] **AC4**: a non-lab host is unreachable from inside the sandbox (egress lock) — proven by a live system test (`tests/system/test_retest_session_system.py`) asserting the lab container is reachable and `example.com` is not.
- **Acceptance criteria — Slice 4** (met — issue #96, ADR-0028 proposed, 2026-07-16):
  - [x] **AC5**: the operator can type a free-text message into the console; it is recorded as a `human_message` transcript event and queued on the live session (a no-op if the session is not live).
  - [x] **AC6**: a queued message is delivered to the agent as a first-class user turn (`user_prompt`) on the next approve/reject, in order — never interrupting a run nor discarding a pending proposal (pure-queue steering).
  - [x] **AC7**: the agent can answer in prose via a non-gated `respond` tool (an `agent_message` event) and the run continues to its next proposal/verdict; messages and `respond` consume no step budget.
  - [x] **AC8**: the SPA sends plain text as a chat message (Send) while `!command` still runs (Run); operator messages render as a distinct turn with a "queued" treatment until delivered; the input disables when the session is over.
- **Acceptance criteria — Slice 5** (met — issue #100, ADR-0029 proposed, 2026-07-17):
  - [x] **AC9**: with free-launch on, the agent's commands auto-run to a verdict with no per-command human approval, while a `set_plan` proposal still pauses for approval (plan changes are always gated).
  - [x] **AC10**: free-launch is settable at session start (`POST /retest-session` body) and toggleable live (`POST /retest-sessions/{id}/free-launch`); enabling mid-session auto-approves any pending command; every toggle is a `free_launch_changed` transcript event and each auto-approval is marked `{"auto": true}`.
  - [x] **AC11**: `max_steps` (both modes) and `max_seconds` (free-launch only, checked at step boundaries) force-conclude the session `given_up`/`inconclusive` with a budget-exhausted reason; both bounds are visible in the SPA.
  - [x] **AC12**: the given-up state renders distinctly from an operator-ended or concluded session.
- **Deferred to later slices** (tracked in epic #87): verdict adjudication UI + FR-09/FR-10/FR-12 integration and retirement of the old batch path (Slice 6).
- **Traces to**: epic #87, issue #88, ADR-0025 (proposed), milestone M6. Supersedes FR-04/FR-05/FR-07/FR-08/FR-09 over time (both paths coexist until Slice 5); NFR-02's reproducibility claim shifts from deterministic re-derivation to a replayable transcript for agentic sessions (stated in ADR-0025).

## 3. Non-functional requirements

### NFR-01 — Verdict reliability
- **Priority**: Must · **Source**: interview 2026-06-11
- **Target**: ≥ **70%** of the evaluation-set findings receive the correct verdict (evaluation goal: all still-open findings identified as still-open). Hard constraint: ambiguous cases must end *inconclusive* — a confidently wrong verdict counts double in the analysis.

### NFR-02 — Full reproducibility
- **Priority**: Must · **Source**: interview 2026-06-11
- **Target**: every verdict re-derivable from the persisted audit trail alone (FR-10 acceptance is the test). Model name/version, prompts, and parameters recorded per LLM call.
- **Status**: verdict re-derivation **met** via ADR-0015 (`audit.rederive_run`). LLM model name is persisted (report/plan `raw`); per-LLM-call prompt/parameter capture is a tracked follow-up (does not affect verdict re-derivability).

### NFR-03 — Safety
- **Priority**: Must · **Source**: interview 2026-06-11 + regulation
- **Target**: non-destructive probes only; allowlist enforced at executor level (FR-06); web app binds to 127.0.0.1 exclusively; no auth in scope (single user, localhost — documented as future work).

### NFR-04 — Data protection (Reglamento TFG 2026 §6)
- **Priority**: Must · **Source**: regulation
- **Target**: no personal data in the repository or in any LLM context; all evaluation data is synthetic or derived from intentionally vulnerable lab targets (the author's own Juice Shop report included), so there is no client/engagement data in this project by construction.

### NFR-05 — Maintainability
- **Priority**: Must · **Source**: development plan (ADR-0001)
- **Target**: CI gates stay green: mypy strict, ruff, coverage ≥ 80% on `src/`, xenon complexity ≤ C absolute.

## 4. Traceability

Every FR has a GitHub issue (`req:FR-xx` label) on the Kanban board; PRs reference issues;
tests are tagged with requirement IDs. The traceability matrix (requirement → issue → PR →
test) is generated for the thesis appendix.
