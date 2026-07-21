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
- **Status**: **superseded by FR-17** (ADR-0033, 2026-07-19). The typed-HTTP-probe plan was retired with the batch execution path; `generate_plan` is deleted. The *plan-generation* intent is repurposed as the generic, user-owned **goal** that seeds an agentic session (`generate_goal`, ADR-0032, under FR-17).
- **Description**: For each finding, the system shall derive a retest plan: an ordered list of typed, non-destructive HTTP probe actions with expected still-open/fixed indicators, generated from the reproduction steps.
- **Acceptance criteria**:
  - [ ] Each plan action is a typed object (no free-form commands) referencing only allowlisted targets.
  - [ ] Each plan states, per action, the indicator that would mark the vulnerability present.

### FR-05 — Human plan review & approval
- **Priority**: Must · **Source**: interview 2026-06-11
- **Status**: **superseded by FR-17** (ADR-0033, 2026-07-19). The batch plan-approval gate (versioned plan rows, single execution chokepoint) is deleted. Human-in-the-loop control is preserved and strengthened under FR-17: the operator approves **every command** before it runs (per-command gate), owns the goal, and can steer or stop the session live.
- **Description**: The web UI shall present each retest plan for review; the user can approve, reject, or edit per finding (and batch-approve). No plan executes without approval.
- **Acceptance criteria**:
  - [x] Unapproved plans are not executable through any code path (enforced server-side, not only in UI).
  - [x] Plan edits are versioned; the executed version is recorded in the audit trail.

### FR-06 — Target authorization allowlist
- **Priority**: Must · **Source**: interview 2026-06-11
- **Status**: satisfied, mechanism changed (ADR-0033, 2026-07-19). With the batch HTTP executor retired, egress control now lives **solely in the agentic sandbox's Docker `--internal` network membership** (ADR-0025) — the container can reach only the connected lab target and nothing else, a strictly stronger guarantee than the HTTP-transport allowlist. The `allowlist.py` HTTP guard was removed (FR-17 6b-iii-b).
- **Description**: The executor shall refuse any action whose target is not on the configured allowlist (default: the lab compose targets). Allowlist changes are explicit configuration, never inferred from report content.
- **Acceptance criteria**:
  - [ ] An approved plan referencing a non-allowlisted host fails closed with an audit-trail entry.
  - [ ] Report-supplied URLs never expand the allowlist (SSRF guard test).

### FR-07 — HTTP probe executor
- **Priority**: Must · **Source**: interview 2026-06-11
- **Status**: **superseded by FR-17** (ADR-0033, 2026-07-19). The httpx batch executor (`retest.py`) is deleted. Verification now runs as **arbitrary gated commands inside the egress-locked sandbox** — HTTP is one case among many (the agent uses `curl` and any lab CLI), and evidence is the tool-agnostic `AgenticEvidence` (ADR-0031) rather than a fixed request/response record.
- **Description**: The system shall execute approved plans via HTTP (httpx), capturing full request/response evidence per step. Probes are verification-only: no destructive payloads, no state-damaging operations.
- **Acceptance criteria**:
  - [ ] Each executed step persists request, response (status/headers/body excerpt), timing, and matched indicators.
  - [ ] Known Juice Shop findings from the evaluation set are detectable end-to-end via HTTP probes.

### FR-08 — Execution sanity checker
- **Priority**: Must · **Source**: interview 2026-06-11 (author's design)
- **Status**: **superseded by FR-17** (ADR-0033, 2026-07-19). The batch-plan deviation guard (`sanity.py`) is deleted — there is no fixed plan to deviate from once the agent decides each step live. The anti-overconfidence intent is preserved differently: a human approves every command, and conservative *inconclusive* handling now lives in the agentic verdict/adjudication path (FR-17, ADR-0030).
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
  - [x] For any completed run, a re-derivation routine reproduces every verdict from stored data only (no re-execution). *(ADR-0033: `audit.rederive_run` re-derives each agentic verdict from its session transcript — the `verdict` event for the agent's record, the latest `verdict_adjudicated` for an operator record — and flags a stored row that has drifted; `GET /api/audit` + `make demo-audit`. `VerdictRecord` carries `created_at`/`actor` for the timestamp+actor trail. Supersedes ADR-0015's batch evidence-rederivation.)*

### FR-11 — Results dashboard (web UI)
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The React SPA shall provide: report/run overview, finding list with verdicts, drill-down to evidence and audit trail, and the agentic retest console (FR-17). Served by FastAPI on localhost only.
- **Acceptance criteria**:
  - [x] The full evaluation flow (extract → goal → retest → verdict) is operable from the UI alone. *(ADR-0013: Vite/React/TS/Tailwind SPA served by FastAPI at `/`, API under `/api`; PDF upload runs FR-01→FR-03 as a background job the UI polls. Originally verified end-to-end in a real browser on a live Ollama backend on the FR-04/05 batch flow (upload → 4 findings → plan → approve → retest → evidence-backed verdict); the flow was reshaped around the FR-17 agentic console in Slice 6b-iii-b (ADR-0033, 2026-07-19) — extract → goal (editable pre-start draft) → agentic retest session → verdict — plus unit/integration coverage of the `/api` chain.)*

### FR-12 — Machine-readable results export
- **Priority**: Must · **Source**: interview 2026-06-11
- **Description**: The system shall export a complete run (findings, plans, verdicts, evidence references, metrics) as a versioned JSON document; the evaluation harness consumes this format.
- **Acceptance criteria**:
  - [x] Export validates against a published JSON schema; the evaluation harness (FR-15) runs on it. — `src/revalid/export.py` (ADR-0016): `RunExport` (reports/findings/verdicts+`AgenticEvidence`/metrics), versioned by `SCHEMA_VERSION` (1.4 since ADR-0033 dropped the batch `plans`); schema generated from the model to `docs/reference/schemas/run-export.schema.json` (`make export-schema`, drift-tested); `GET /api/export` + `/api/export/schema`; `make demo-export` validates a run against the published schema.

### FR-13 — Pluggable LLM backends (Claude primary, local fallback)
- **Priority**: Should · **Source**: interview 2026-06-11
- **Description**: The LLM layer (Pydantic AI) shall be model-agnostic: Claude API as primary; a local model (Ollama) configurable as fallback and as comparison condition in the evaluation.
- **Acceptance criteria**:
  - [ ] Switching backends is configuration-only (no code change); both run the extraction test suite.
  - [x] The active backend is a **user-editable, DB-persisted setting** changeable at runtime (env vars seed a fresh DB; the stored row is then authoritative). Model discovery + a connection test surface in the SPA `/settings` view. (ADR-0021)

### FR-14 — Browser-based probes (Playwright)
- **Priority**: Could · **Source**: interview 2026-06-11
- **Status**: **dropped, subsumed by FR-17** (ADR-0033, 2026-07-19). The dedicated Playwright browser executor (`browser.py`, ADR-0018) is deleted. DOM/JS-dependent verification is reachable within FR-17: the sandbox agent can run browser-capable tooling as a command, so a distinct browser-probe path is no longer warranted (a Kali-tooling sandbox image is tracked separately, #105).
- **Description**: For findings not verifiable at HTTP level (DOM/JS-dependent), the executor may support Playwright-driven browser probes under the same approval, allowlist, and audit constraints.
- **Acceptance criteria**:
  - [~] At least one stored-XSS-class Juice Shop finding verifiable only in-browser got a correct verdict. — `src/revalid/browser.py` (ADR-0018, deleted in FR-17 6b-iii-a): a `browser-xss` Playwright probe (optional `browser` extra) verified Juice Shop's DOM XSS in a real browser under the same FR-05/FR-06/FR-10 constraints (`guarded_run` was executor-agnostic; browser verdicts re-derived via the shared `assess_evidence`). Pipeline + assessor were unit-tested with a canned runner; the live-lab still-open verdict was asserted by `tests/system/test_browser_xss_system.py` (nightly `system-tests.yml`, also deleted). Exemplar was DOM (browser-only-verifiable) XSS, not persisted — same probe kind/assessor generalized.

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
  - [x] **AC11** *(superseded by AC23 / ADR-0034)*: `max_steps` (both modes) bounds the session — no longer a give-up but a **pause for guidance**; the `max_seconds` wall-clock budget is **removed**.
  - [x] **AC12** *(superseded by AC23 / ADR-0034)*: the give-up state is retired; a bounded/stuck session renders as a **needs-guidance pause** (Keep going / Conclude), distinct from an operator-ended or concluded session.
- **Acceptance criteria — Slice 6a** (met — issue #102, ADR-0030 proposed, 2026-07-18):
  - [x] **AC13**: a concluded (or given-up) session's verdict is auto-persisted as an agentic `VerdictRecord` (`actor="agent"`, `source="agentic"`, evidence-free, session-linked) with no human action, so it is queryable at `GET /api/verdicts`, appears in the FR-12 export, and re-derives under the FR-10 audit — without touching the frozen domain `Verdict`/`Evidence` type (polymorphic storage).
  - [x] **AC14**: the operator can accept or override the agent's verdict (`POST /retest-sessions/{id}/adjudicate`); adjudication appends a `verdict_adjudicated` transcript event **and** a superseding operator verdict (`actor="operator"`, higher id ⇒ latest-per-finding), never mutating the agent's record (append-only; FR-10 intact).
  - [x] **AC15**: FR-10 re-derivation reproduces an agentic verdict from its session transcript (the `verdict` event for the agent's record, the latest `verdict_adjudicated` for an operator record) and flags a stored row that has drifted from it; FR-12 `VerdictExport` flattens to one shape (+ `source`/`session_id`/optional `evidence`), `SCHEMA_VERSION` 1.1 → 1.2.
- **Acceptance criteria — Slice 6b-i** (met — issue #104, ADR-0031 proposed, 2026-07-18):
  - [x] **AC16**: a concluded (or given-up) agentic verdict carries flexible, tool-agnostic `AgenticEvidence` — the agent's explanation plus the *real* last command's output (command/stdout-stderr excerpt/exit code/timing) captured from the transcript, not the model restating it — queryable at `GET /api/verdicts`, present in the FR-12 export, and shown in the SPA verdict view. A verdict reached with no command run is explanation-only and still valid.
  - [x] **AC17**: the HTTP `Evidence`/batch verdict shape is unchanged (one JSON column holds either, keyed by `source`); the export schema bumps 1.2 → 1.3 (regenerated + drift-tested).
- **Acceptance criteria — Slice 6b-ii** (met — issue #107, ADR-0032 proposed supersedes ADR-0027, 2026-07-19):
  - [x] **AC18**: the guiding plan is a **user-owned goal** — the agent no longer proposes it (`set_plan` and its `awaiting_plan`/`plan_proposed/approved/rejected` orchestration are removed, 6b-ii-a); a generic, finding-agnostic `generate_goal` seeds it at session start (shown in the "Current goal" panel, given to the agent), degrading to an empty goal on generation failure without blocking start.
  - [x] **AC19**: the operator (alone) edits or regenerates the goal live (`POST /retest-sessions/{id}/goal` + `/goal/regenerate`); the change updates the panel (`plan_updated`) immediately and reaches the agent as a first-class user turn on its next approve/reject (pure-queue), never interrupting a run.
- **Acceptance criteria — Slice 6b-iii-a** (met — issue #110, ADR-0033 proposed, 2026-07-19):
  - [x] **AC20**: the batch execution path is deleted end-to-end (backend) — `approval.py`/`retest.py`/`sanity.py`/`browser.py`, the batch plan/approve/retest REST endpoints, the batch domain types (`Probe`/`RetestPlan`/`PlanStatus`/`Verdict`/`Evidence`), and `PlanRecord` — with the full gate green; FR-09/10/12 now have exactly one (agentic) implementation.
  - [x] **AC21**: `VerdictRecord`, `VerdictExport`, and the FR-10 audit collapse from polymorphic (batch/agentic) to a single agentic shape — the `source` discriminator and batch-only columns are gone, the audit re-derives only from the transcript; the FR-12 export drops `plans`, `SCHEMA_VERSION` 1.3 → 1.4 (regenerated + drift-tested).
- **Acceptance criteria — Slice 6b-iii-b** (met — issue #110, ADR-0033 proposed, 2026-07-19):
  - [x] **AC22**: the finding flow is **extract → goal → retest → verdict**; no batch
    stage/hook/client-fn/`Plan` type remains and the SPA calls no removed endpoint. The
    Goal stage generates an editable pre-start draft goal (no session), and **Start retest**
    launches a session seeded with it; the console is the only retest path, relaid out as
    chat + right-editable goal + bottom terminal, with live goal edit, the command gate, chat
    steering, and adjudication intact and an in-progress session surviving reload.
- **Acceptance criteria — Slice 8 (pause-and-ask)** (met — issue #117, ADR-0034 proposed, 2026-07-19):
  - [x] **AC23**: the session never *gives up*. Reaching the `max_steps` budget, or the agent concluding `inconclusive` (reinterpreted as "exhausted my options"), **pauses** the session in the non-terminal `needs_guidance` state with the sandbox kept alive and no verdict written; there is **no wall-clock budget**. The operator **keeps going** (`POST …/continue {extra_steps?}` — raises the budget and resumes, re-opening a held command's gate or re-running the agent with queued guidance) or **concludes** (`POST …/conclude {status, rationale}` — the only path that records `inconclusive`, `actor="operator"`); chat and terminal commands stay usable while paused, and the SPA shows a pause banner instead of a give-up one.
- **Remaining**: **none for the core**; the Kali-tooling sandbox image is tracked separately (#105) and does not gate FR-17.
- **Traces to**: epic #87, issue #88, ADR-0025 (accepted), milestone M6. **Supersedes FR-04/FR-05/FR-07/FR-08 and drops FR-14** — the batch path was deleted in Slice 6b-iii-a (ADR-0033), leaving the agentic console the single retest implementation; FR-09 stays satisfied by agentic verdicts and FR-06 is now enforced by sandbox network isolation. NFR-02's reproducibility claim is a replayable transcript for agentic sessions (stated in ADR-0025).

### FR-18 — Reports chat assistant (corpus Q&A)
- **Priority**: Should · **Source**: change request 2026-07-20 (ADR-0036, issue #136)
- **Description**: The system shall provide a **read-only conversational assistant**, reachable from a **Chat** tab in the SPA's left navigation, that answers natural-language questions about the whole corpus of ingested reports, findings, and retest verdicts — e.g. *"how many reports do we have?"*, *"how many findings relate to SQL injection?"*, *"which report has the most criticals?"*. It is a Pydantic AI agent with typed, **read-only** DB query tools (reusing the FR-13 configured backend); it never mutates data and never launches a retest. Conversation threads are **persisted** so a chat survives a page reload.
- **Acceptance criteria** (met — issue #136, ADR-0036 proposed, 2026-07-20):
  - [x] **AC1**: a **Chat** tab in the left nav opens the assistant; the operator sends a message and receives an agent reply over `POST /api/chats/{id}/messages`. *(`frontend/src/routes/Chat.tsx`, `_register_chat_message_route`.)*
  - [x] **AC2**: counts are grounded in read-only tools — `get_corpus_overview` (reports by status, findings by severity, latest verdict per finding), `search_findings` (exact `total` by keyword/severity/report even when the row list is capped), `list_all_reports`, `finding_detail` — so "how many …" answers are exact, not estimated. *(`src/revalid/reports_chat.py`.)*
  - [x] **AC3**: conversation threads are persisted (`chat_sessions`/`chat_messages`) and survive reload; the operator starts new threads and revisits or deletes prior ones (`GET`/`POST`/`DELETE /api/chats…`). *(FR-18 chosen persisted over ephemeral.)*
  - [x] **AC4**: read-only + backend-agnostic — no tool mutates data or starts a retest, and the agent is built from the FR-13 setting (`build_model`); backend tools + endpoints are unit/integration-tested with a Pydantic AI stand-in, the SPA view has vitest coverage, all CI gates green.
  - [x] **AC5** *(enhancement, ADR-0038, 2026-07-21)*: the reply **streams token-by-token** as it is generated. `POST /api/chats/{id}/messages/stream` returns Server-Sent Events (one `event: token` frame per delta, terminal `event: done`); the SPA grows the assistant bubble live and hands off to the persisted thread on completion. The endpoint is async (`stream_answer` over `agent.run_stream`); the blocking `…/messages` endpoint is kept as a fallback. *(`reports_chat.stream_answer`, `_register_chat_message_route`, `frontend/src/api/client.ts` `streamChatMessage`.)*
- **Traces to**: issue #136, ADR-0036 (accepted) + ADR-0038 (proposed — streaming, not yet on `main`; issue #140), milestone M6.

### FR-19 — CVSS + MITRE ATT&CK enrichment of findings
- **Priority**: Should · **Source**: change request 2026-07-20 (ADR-0037, issue #144)
- **Description**: The system shall attach to each ingested finding a **CVSS code** (base vector + score) and a **MITRE ATT&CK technique mapping**, realising the §2.1.3 requirement that findings be mapped onto the standard reference frameworks. Values **stated in the report** are captured verbatim; when the report is silent the extraction model **derives** a best-estimate CVSS v3.1 vector/score and the most applicable ATT&CK technique IDs, each flagged `inferred` so an estimate is always distinguishable from a stated value. The taxonomy fields are classificatory metadata only — they never feed the retest verdict (ADR-0037).
- **Acceptance criteria**:
  - [x] **AC1**: ingestion attaches `cvss` (`vector`, `base_score`, `inferred`) and `mitre` (`techniques`, `inferred`) to every finding; a stated code maps through with `inferred=false`, an absent one is derived with `inferred=true`. *(`ExtractedFinding`, `_to_finding`, extraction instructions.)*
  - [x] **AC2**: the fields persist as first-class columns and survive the FR-16 version round trip — not only inside the `raw` audit blob. *(`FindingVersionRecord.cvss`/`.mitre`, `from_domain`/`to_domain`.)*
  - [x] **AC3**: extraction + round-trip are unit-tested with a Pydantic AI stand-in (stated / inferred / absent), mypy `--strict`, ruff and coverage all green.
  - [ ] **AC4**: the `/api` finding payload and the SPA finding view surface the CVSS code and ATT&CK techniques with their `inferred` provenance, and the evaluation ground truth is tagged with them. *(Follow-up slice.)*
- **Traces to**: issue #144, ADR-0037 (accepted), milestone M2.

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
