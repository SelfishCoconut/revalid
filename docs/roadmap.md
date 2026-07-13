# Roadmap & resume point

> **Purpose**: durable implementation plan. Any session (human or AI, fresh context) can
> pick up the project from this file. Keep it current: when a milestone's state changes,
> update the checkboxes and the "Current state" line **in the same PR**.

## How to resume a session

1. Read `CLAUDE.md` (rules), then this file (where we are), then the [Kanban board](https://github.com/users/SelfishCoconut/projects/1) (what's in flight).
2. Requirements live in [`docs/requirements/srs.md`](requirements/srs.md) (FR/NFR by ID); architecture decisions in [`docs/adr/`](adr/README.md) — ADR-0002 fixes the stack.
3. Work item = one issue → feature branch → PR with "How to validate" → CI (`Verify`) → Álvaro validates (`Validate`) → squash merge. `main` is protected; direct pushes fail.

## Current state (update me)

**2026-07-13** — **M1 walking skeleton RELEASED as [`v0.1.0`](https://github.com/SelfishCoconut/revalid/releases/tag/v0.1.0).** **Done:** package layout (FastAPI app factory, SQLite via SQLAlchemy, domain schemas); FR-02 ingest (#23); FR-06 allowlist/SSRF guard (#34); **FR-07 probe executor + FR-09 evidence-backed verdicts (#12/#14)** — one hardcoded SQLi login-bypass probe (`src/revalid/retest.py`) runs against the Juice Shop lab through the FR-06 `AllowlistTransport`, captures request/response/timing evidence, and yields a `still_open`/`fixed`/`inconclusive` `Verdict` with a machine-readable reason code; persisted (`verdicts` table) and exposed at `POST /findings/{id}/retest` + `GET /verdicts`. `lab/docker-compose.yml` (Juice Shop **v17.1.1**, pinned) + `make lab-up`/`lab-down`; `make demo-walking-skeleton` prints ingest→probe→verdict; the system test asserts `still_open` against the live lab (nightly `system-tests.yml` brings the lab up). Verified end-to-end locally (real HTTP 200 + JWT → `still_open`); security review + `codebase-sanity` audit both clean before the tag. **Process:** ADR-0004 right-sized the solo-dev workflow — kept the Kanban board, disabled the forced codebase-memory discovery gate (MCP still available on demand), reserved full PR ceremony for FR/NFR PRs. ADR-0005 removed the `ai-compliance-auditor` agent (§6 compliance is Álvaro's own judgement). CI on `main` is green. **Next action: open M2 — FR-01 PDF ingestion (#6) and FR-03 LLM extraction (#8); the scrubbed evaluation report (side item below) is the M2 fixture.**

Pending side items: Álvaro's Juice Shop pentest report must be scrubbed (no real engagement data) and added to `tests/data/` — it defines the evaluation ground truth and what M2 must parse.

## Milestones (= GitHub milestones; each closes with a release)

### M1 — Walking skeleton  ·  FR-02 #7, FR-06 #11, FR-07 #12, FR-09 #14
Thin end-to-end slice proving the architecture. Scope deliberately minimal:
- [x] Package layout per ADR-0002: FastAPI app factory, SQLite via SQLAlchemy, domain models as Pydantic schemas (`Finding` done; `Probe`/`Verdict` arrive with FR-07/FR-09)
- [x] FR-02 (minimal): ingest a simple structured JSON findings file from `tests/data/` (full DefectDojo mapping can wait)
- [x] FR-06: allowlist config + executor-level enforcement (SSRF guard test from SRS) — `src/revalid/allowlist.py`, #11
- [x] FR-07 (minimal): ONE hardcoded SQLi login-bypass probe against local Juice Shop through the FR-06 transport, capturing request/response/timing evidence — `src/revalid/retest.py`, #12
- [x] FR-09 (minimal): `still_open`/`fixed`/`inconclusive` verdict linked to evidence with a machine-readable reason code, exposed at `POST /findings/{id}/retest` + `GET /verdicts` — #14
- [x] `lab/docker-compose.yml` with Juice Shop (pinned v17.1.1) — fills in the `retest-lab` skill + system-tests CI job
- [x] `scripts/demo/walking_skeleton.py` (`make demo-walking-skeleton`): one command, ingest→probe→verdict printed
- [x] **Done**: demo + system test green end-to-end against the lab; released as `v0.1.0` (2026-07-13).
- **No LLM and no frontend in M1** — deterministic slice first.

### M2 — Report understanding  ·  FR-01 #6, FR-03 #8, FR-13 #18
- [ ] PDF ingestion pipeline (FR-01) — evaluation report as the test fixture (scrubbed)
- [ ] LLM finding extraction with Pydantic AI + Claude API, schema-validated, TestModel-based unit tests (FR-03)
- [ ] Model-agnostic config; Ollama fallback runs the same extraction suite (FR-13)
- **Done when**: the scrubbed Juice Shop PDF report yields ≥90% well-formed findings (FR-03 criterion); release `v0.2.0`.

### M3 — Plan & approve  ·  FR-04 #9, FR-05 #10, FR-11 #16
- [ ] Retest-plan generation: typed probe actions + expected indicators from reproduction steps (FR-04)
- [ ] Server-side approval gate; plans versioned; nothing unapproved executes (FR-05)
- [ ] React SPA: plan review/edit/approve + results dashboard with evidence drill-down (FR-11); frontend toolchain enters CI
- **Done when**: full flow operable from the UI alone (FR-11 criterion); release `v0.3.0`.

### M4 — Trust & audit  ·  FR-08 #13, FR-10 #15, FR-12 #17
- [ ] Execution sanity checker: plan-deviation blocking + ambiguity→inconclusive (endpoint-moved test case) (FR-08)
- [ ] Full audit trail; verdict re-derivation routine (FR-10, NFR-02)
- [ ] Versioned JSON export with schema (FR-12)
- **Done when**: re-derivation reproduces all verdicts from stored data; release `v0.4.0`.

### M5 — Evaluation  ·  FR-15 #20, (FR-14 #19 Could)
- [ ] Ground truth: deliberately vulnerable Juice Shop version pinned in lab; expected verdict per finding
- [ ] Evaluation harness: metrics table (correct/wrong/inconclusive, timing) from a run export (FR-15)
- [ ] NFR-01 measured: ≥70% correct verdicts, zero confidently-wrong on ambiguity
- [ ] If time allows: Playwright probes for one DOM-dependent finding (FR-14)
- **Done when**: Results-chapter numbers exist and are reproducible; release `v1.0.0`.

## Thesis track (parallel, `thesis` label)

Write chapters as their content becomes real, not at the end: Introduction & objectives (any time) → State of the art (during M2) → Design (after M3, from ADRs + C4 docs) → Implementation (during M4) → Evaluation/Results (M5) → Conclusions + **AI declaration** (generated by the `ai-declaration` skill, last). Run `codebase-sanity` before each release. §6 AI-usage compliance is Álvaro's direct responsibility (ADR-0005), not an agent's.
