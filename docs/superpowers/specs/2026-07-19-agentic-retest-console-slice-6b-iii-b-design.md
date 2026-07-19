# FR-17 Slice 6b-iii-b — SPA finding-flow reshape (design)

- **Epic:** #87 · **Issue:** #110 · **ADR:** 0033 (proposed — this implements its §2.2 reshape) · **Milestone:** M6 (**closes FR-17**)
- **Depends on:** 6b-iii-a (backend batch retirement, merged in #111). **The last FR-17 slice.**
- **Date:** 2026-07-19

## 1. Problem

6b-iii-a deleted the batch execution *backend*; the SPA still carries the batch
finding wizard (Plan → Approve → Retest stages) that calls now-removed endpoints
(harmless 404s) and buries the agentic console behind a plan-approve gate. This
slice reshapes the finding UI so the agentic console **is** the retest, closing
FR-17. Two design questions from the 6b-iii spec (§3) are now settled with Álvaro:

- **Goal stage = pre-start draft** (option b): the operator generates + edits the
  goal *before* the session/sandbox exists, then "Start retest" launches with it.
- **Console layout** = one integrated view: **chat** as the main area (to guide the
  retest and watch it), **terminal** docked at the bottom, **goal** as an editable
  box on the **right**.

## 2. Decision

Collapse the 5-stage finding wizard to **four stages — `extract → goal → retest →
verdict`** — with the agentic console as the retest stage, and add the minimal
backend surface the pre-start-draft goal needs. This is the implementation of
ADR-0033 §2.2; no new ADR (it introduces no new architectural decision beyond the
already-recorded reshape). It also folds in the two cleanups 6b-iii-a deferred.

### 2.1 Backend — the only new API (`app.py` + `retest_session.py`)

1. **`POST /api/findings/{id}/goal/draft` → `{ "steps": [...] }`** — builds the goal
   agent and runs `generate_goal(finding)` for the finding's current version,
   **without** creating a session and **without** persistence. Powers the Goal
   stage's generate/regenerate. Reuses the existing goal-agent DI
   (`get_goal_agent`/`GoalAgentDep`) and `run_regenerate_goal`'s generation path.
2. **`POST /api/findings/{id}/retest-session` gains optional `initial_goal:
   list[str]`.** When present, `create_session` records it and `run_first_step`
   **seeds** the session with it (emits the initial `plan_updated`, prepends it to
   the agent prompt) *instead of* generating a fresh goal. When absent → today's
   generate-at-start behavior, so the headless/free-launch entry (FR-15 eval) is
   unchanged. Seeding a supplied goal is best-effort-free (no LLM call), so start
   stays responsive.
3. **`GET /api/findings/{id}/retest-sessions` → `[RetestSessionSummary]`** — id,
   status, created_at, and the session's verdict status if any, newest first. The
   retest stage uses it to re-find an **in-progress** session after reload (verdicts
   only link *concluded* sessions via `session_id`), and `FindingLayout` uses it to
   know "retest reached." Read-only projection of `RetestSessionRecord`.

No schema/DB change: `initial_goal` is consumed at start (seeds the transcript),
not stored as a column; the draft is never persisted.

### 2.2 Frontend — stages & teardown

**Stages** (`App.tsx` routing, `PipelineTrack`, `FindingLayout`, `useFindingStage`):
- `PipelineTrack.STAGES` → `["extract", "goal", "retest", "verdict"]`.
- `pipelineReach` reshapes from `{planned, approved, retested}` to `{sessionExists,
  hasVerdict}`: **extract** always reached; **goal** is the current stage right
  after extract; **retest** reached once ≥1 session exists for the finding;
  **verdict** reached once ≥1 verdict exists. The final node still borrows the
  verdict tone.
- `FindingLayout` drops `usePlans`; adds `useFindingSessions(findingId)` (endpoint
  #3) + keeps `useVerdicts`. Its Outlet context drops `plans`/`currentPlan`/
  `hasPlan`/`approved` and gains `sessions`/`latestSession`.

**Delete:** `routes/stages/PlanStage.tsx`, `ApproveStage.tsx`, the batch
`RetestStage.tsx` body; `components/PlanHistory.tsx`, `PlanActions.tsx`;
`hooks/usePlans.ts`, the batch `useRetest`; `lib/planActions.ts`; the client fns
`generatePlan`/`approvePlan`/`rejectPlan`/`revisePlan`/`listPlans`/`retest` and the
`Plan` type; and every batch stage test.

### 2.3 Goal stage (new — `routes/stages/GoalStage.tsx`)

- On mount, fetch a goal **draft** via endpoint #1 (React Query, cached per finding;
  infinite staleness — a manual **Regenerate** refetches). First visit generates;
  revisits within the session hit cache; a full reload regenerates once.
- Render the draft as an **editable box**: a textarea (one step per line) with the
  steps, **Regenerate**, and **Start retest**. Draft edits live in component/query
  state only — **not persisted** (YAGNI; single-user local tool).
- **Start retest** → `startRetestSession(findingId, { initial_goal: editedSteps })`
  → navigate to `/findings/{id}/retest`. If steps are empty, start still works
  (empty goal, same as a generation failure today).
- Includes the stage's `NotesThread` (FR-16), like the other stages.

### 2.4 Agentic retest stage (`routes/stages/RetestStage.tsx` hosts the console)

- The stage resolves the finding's **latest** session from `useFindingSessions`;
  renders `<RetestSession sessionId={...} />`. If no session exists, redirect to the
  goal stage (consistent with `FindingLayout`'s "ahead of progress" redirect). A
  **New retest** affordance links back to the goal stage to launch a fresh session.
- `RetestSession` is refactored to take `sessionId` as a **prop** (today it reads
  `useParams().id`); the standalone `/retest-sessions/:id` route stays as a
  deep-link that passes the param through, so nothing else breaks.
- **Relayout** (presentation only — no `/api`/WS/state change): from the current
  vertical stack (header → full-width goal panel → chat → terminal → input) to:

  ```
  ┌ header (status · budget · free-launch · End) ───────────┐
  ├───────────────────────────────┬─────────────────────────┤
  │ CHAT (main, scrolling)        │ GOAL (right, editable)   │
  │  agent turns · command cards  │  steps + [Edit][Regen]   │
  │  approve/reject · verdict      │                         │
  │  · adjudication                │                         │
  ├───────────────────────────────┴─────────────────────────┤
  │ TERMINAL (docked bottom, collapsible, full width)        │
  ├──────────────────────────────────────────────────────────┤
  │ operator input — message / !command                      │
  └──────────────────────────────────────────────────────────┘
  ```
  The goal `Panel` moves from a full-width top block into a right column beside the
  chat; the terminal and operator input keep their bottom docking. All behavior
  (WS stream, gate, chat queue, live goal edit/regenerate, adjudication) is
  unchanged. Two-column on desktop; stacks to single-column on narrow screens.

### 2.5 Verdict stage (`routes/stages/VerdictStage.tsx` — agentic-only)

The latest (adjudicated-if-any) agentic verdict for the finding: status badge +
`AgenticEvidence` (explanation + command + output) via the `EvidenceView` agentic
branch. Remove the batch verdict cards and the HTTP `Evidence` branch. Links to the
retest stage to open the backing session's console.

### 2.6 Cleanups folded in (deferred from 6b-iii-a)

- **`docs/architecture/c4.md`** — reshape the Level-2 (M1 slice) and Level-3
  (FR-11 ingest→verdict, FR-08 guarded execution) sequence diagrams to the agentic
  flow; they still name deleted modules (`retest.py`, `execute_approved_plan`,
  `sanity.py`). Authored diagrams must track the code (CLAUDE.md).
- **Delete `src/revalid/allowlist.py` + `tests/unit/test_allowlist.py`** — orphaned
  after 6b-iii-a; FR-06 egress is the sandbox Docker `--internal` network (ADR-0033).
  Confirm no `src` import remains (vulture can't see it because its test keeps the
  symbols "used").

## 3. Data flow (pre-start-draft goal)

```
Goal stage mount ─▶ POST /findings/{id}/goal/draft ─▶ {steps}  (cached; no session)
   operator edits steps (local) / Regenerate ─▶ refetch draft
   Start retest ─▶ POST /findings/{id}/retest-session {initial_goal: steps}
                     └▶ create_session(initial_goal) ─▶ run_first_step seeds
                        plan_updated + prompt (no generation) ─▶ session live
   navigate ─▶ /findings/{id}/retest ─▶ resolve latest session ─▶ console
```

## 4. Acceptance criteria (→ SRS FR-17 AC22; closes the umbrella)

1. The finding flow is **extract → goal → retest → verdict**; no batch stage,
   hook, client fn, or `Plan` type remains; the SPA calls no removed endpoint.
2. **Goal stage (pre-start draft):** generates an editable draft goal (no session),
   Regenerate refetches, and **Start retest** creates the session seeded with the
   edited goal and opens the console — verified in a browser.
3. **Console** renders as the integrated view (chat main, goal right-editable,
   terminal bottom); live goal edit/regenerate, the command gate, chat steering, and
   adjudication all still work; an in-progress session survives a reload (via
   `GET …/retest-sessions`).
4. **Verdict stage** shows the agentic determination + `AgenticEvidence`; FR-16
   versioning/notes and the FR-15 eval still work over agentic verdicts.
5. `allowlist.py` deleted; `c4.md` diagrams show the agentic flow; `codebase-sanity`
   finds no batch remnant. ADR-0025 ratifiable; M6 release can close (Álvaro's call).

## 5. Test plan

- **backend** — unit/integration for the three new endpoints: goal-draft returns
  steps without a session; `initial_goal` seeds (assert the first `plan_updated`
  equals the supplied steps, no generation); the sessions list projects newest-first
  with verdict status. TestModel/FunctionModel stand in for the LLM (no live calls).
- **frontend** — delete batch stage tests; update `FindingLayout`/`PipelineTrack`
  tests to four stages + `pipelineReach` new inputs; add `GoalStage` tests
  (draft render/edit/regenerate/start with `initial_goal`) and a retest-stage
  test (resolves latest session, redirects when none); keep the `RetestSession`
  behavior tests, updated for the `sessionId` prop + two-column layout (assert
  structure, not pixels).
- **schema/demos** — none new (6b-iii-a already at 1.4). `make demo-retest-session`
  still green.
- **release gate** — `codebase-sanity` before the M6 release.

## 6. Out of scope / deferred

- Persisting the pre-start goal draft (regenerates on reload — accepted).
- Per-finding session **history** UI beyond "latest + New retest" (the list endpoint
  exists; a fuller history list can come later if wanted).
- Any further console layout reshuffling — easy follow-ups once this lands.
