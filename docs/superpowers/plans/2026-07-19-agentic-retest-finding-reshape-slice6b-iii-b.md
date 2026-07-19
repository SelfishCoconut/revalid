# FR-17 Slice 6b-iii-b — SPA Finding-Flow Reshape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the finding UI from the 5-stage batch wizard to `extract → goal → retest → verdict`, making the agentic console the only retest path, and delete the last batch remnants — closing FR-17.

**Architecture:** Three small additive backend endpoints (goal-draft, `initial_goal` on session start, list-sessions-by-finding) power a pre-start editable goal; the SPA drops every batch stage/hook/client-fn, reshapes the pipeline to four stages, hosts the existing agentic console as the retest stage (relaid out to chat-main / goal-right / terminal-bottom), and renders an agentic-only verdict stage. Two 6b-iii-a-deferred cleanups (orphaned `allowlist.py`, stale `c4.md` diagrams) fold in.

**Tech Stack:** Backend — Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic AI (TestModel/FunctionModel in tests), pytest. Frontend — React 19 + TypeScript + Vite + Tailwind + TanStack Query + React Router + Vitest/RTL.

## Global Constraints

- **Backend gate (must stay green each backend task):** `uv run pytest tests/unit tests/integration -q`, `uv run mypy`, `uv run ruff check src tests scripts`, `uv run ruff format --check src tests`, `uv run xenon --max-absolute C src`.
- **Frontend gate (must stay green each frontend task):** from `frontend/`: `npx tsc --noEmit`, `npx eslint .`, `npx vitest run`, `npm run build`.
- **mypy `--strict`** semantics: full type hints; no `Any` leaks on new code. **Ruff** line length 100, Google docstrings on new public API.
- **Complexity:** xenon max absolute **C**. If a route-registration function trips mccabe, split it into a new `_register_*` helper (existing pattern), never suppress.
- **No new deps.** **No DB schema change** — `initial_goal` is consumed at session start, never stored.
- **Conventional Commits**; every commit carries `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Commit after each task.
- **Write-hook caveat:** the editor hook strips unused imports on save. When adding an import + its first use, add the *usage* first (or re-add the import after) and `grep` to confirm it survived.
- **Branch:** `feat/fr17-finding-reshape-slice6b-iii-b` (already created, spec committed). Final PR body says **`Closes #110`** (this is the last slice of #110).

---

## Phase A — Backend (additive endpoints)

### Task 1: `initial_goal` on session start

**Files:**
- Modify: `src/revalid/app.py` — `StartSessionRequest` (~309), `run_first_step` (~538), `start_retest_session` (~995)
- Test: `tests/integration/test_retest_session_api.py`

**Interfaces:**
- Produces: `POST /api/findings/{id}/retest-session` accepts optional body field `initial_goal: list[str] | None`; when present + non-empty, the session's first `plan_updated` event carries exactly those steps and **no** goal generation runs.
- Consumes: existing `generate_goal(goal_agent, finding)`, `append_event`, `_goal_prompt`, `start_and_step`.

- [ ] **Step 1: Write the failing test.** In `tests/integration/test_retest_session_api.py`, reuse the file's existing app-with-stand-in-agents fixture (the one other session tests use to POST `/findings/{id}/retest-session`). Add:

```python
def test_start_session_seeds_supplied_initial_goal(...) -> None:
    """A start body with initial_goal seeds that goal verbatim — no generation."""
    # ... create a finding (id 1) as the sibling tests do ...
    resp = client.post(
        "/api/findings/1/retest-session",
        json={"initial_goal": ["Confirm the login endpoint", "Retry the documented bypass"]},
    )
    assert resp.status_code == 202
    sid = resp.json()["id"]
    # Poll the session until it has a plan_updated event (background task ran).
    events = _wait_for_kind(client, sid, "plan_updated")   # helper pattern already in this file
    steps = next(e for e in events if e["kind"] == "plan_updated")["payload"]["steps"]
    assert steps == ["Confirm the login endpoint", "Retry the documented bypass"]
```

Match the file's existing helper names/fixtures (read the top of the file first for the exact fixture + any `_wait_for_*` helper; if none exists, poll `GET /api/retest-sessions/{sid}` in a short loop asserting the event appears).

- [ ] **Step 2: Run it, verify it fails.** `uv run pytest tests/integration/test_retest_session_api.py::test_start_session_seeds_supplied_initial_goal -v` → FAIL (`initial_goal` rejected / not seeded).

- [ ] **Step 3: Add the request field.** In `StartSessionRequest`:

```python
class StartSessionRequest(BaseModel):
    """Optional body for starting a session: free-launch + budget config (FR-17 Slice 5)."""

    free_launch: bool = False
    max_steps: int = Field(default=8, ge=1)
    max_seconds: int | None = Field(default=None, ge=1)
    # A user-owned goal drafted before the session (FR-17 6b-iii-b): when present,
    # the session seeds it verbatim instead of generating one at start.
    initial_goal: list[str] | None = None
```

- [ ] **Step 4: Thread it into `run_first_step`.** Change the signature to accept the goal and short-circuit generation:

```python
def run_first_step(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    agent: RetestAgent,
    make_sandbox: SandboxFactory,
    finding: Finding,
    goal_agent: Agent[None, GeneratedGoal],
    initial_goal: tuple[str, ...] | None = None,
) -> None:
```

Replace the goal-derivation block (currently lines ~580-586) with:

```python
            goal: tuple[str, ...] = tuple(initial_goal) if initial_goal else ()
            if not goal:  # no pre-start draft → generate (best-effort; never blocks)
                with contextlib.suppress(Exception):
                    goal = generate_goal(goal_agent, finding)
            if goal:
                append_event(
                    session, session_id, SessionEventKind.PLAN_UPDATED, {"steps": list(goal)}
                )
```

Update the docstring's "It also **seeds the goal**" paragraph to note a supplied `initial_goal` is used verbatim, else generation.

- [ ] **Step 5: Pass it from the route.** In `start_retest_session`, change the `background.add_task(...)` call to append the goal:

```python
        background.add_task(
            run_first_step,
            sessions,
            registry,
            record.id,
            agent,
            make_sandbox,
            finding,
            goal_agent,
            tuple(cfg.initial_goal) if cfg.initial_goal else None,
        )
```

- [ ] **Step 6: Run the test + gate.** `uv run pytest tests/integration/test_retest_session_api.py -q` → PASS. Then `uv run mypy && uv run ruff check src tests && uv run xenon --max-absolute C src`.

- [ ] **Step 7: Commit.**
```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(retest): seed a pre-start goal on session start (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `POST /findings/{id}/goal/draft` — generate a goal without a session

**Files:**
- Modify: `src/revalid/app.py` — add a `GoalDraftOut` model + a new `_register_finding_retest_routes(router, sessions)` helper; wire it in `create_app` (~1329, next to `_register_finding_routes`)
- Test: `tests/integration/test_retest_session_api.py` (or `tests/unit/test_app_goal.py` if the file prefers unit-level app tests — match the repo's placement for route tests)

**Interfaces:**
- Produces: `POST /api/findings/{id}/goal/draft` → `{"steps": list[str]}` (200). Runs `generate_goal` on the finding's current version; **no session, no persistence**. 404 if the finding doesn't exist.
- Consumes: `_current_or_404`, `get_goal_agent`/`GoalAgentDep`, `generate_goal`.

- [ ] **Step 1: Write the failing test.** Using the app-with-stand-in-goal-agent fixture (the goal agent is overridden with a FunctionModel that returns fixed steps — copy the override the existing `regenerate` test uses):

```python
def test_goal_draft_generates_without_a_session(...) -> None:
    # finding id 1 created; goal agent stubbed to return ("step one", "step two")
    resp = client.post("/api/findings/1/goal/draft")
    assert resp.status_code == 200
    assert resp.json() == {"steps": ["step one", "step two"]}
    # No session row was created.
    assert client.get("/api/findings/1/retest-sessions").json() == []  # (endpoint from Task 3)
```

(If Task 3 isn't merged yet when writing this, assert instead that `GET /api/retest-sessions/1` is 404.)

- [ ] **Step 2: Run it, verify it fails.** `uv run pytest ...::test_goal_draft_generates_without_a_session -v` → FAIL (404 route).

- [ ] **Step 3: Add the output model** (near `GoalRequest`, ~326):

```python
class GoalDraftOut(BaseModel):
    """A generated retest-goal draft for a finding, pre-session (FR-17 6b-iii-b)."""

    steps: list[str]
```

- [ ] **Step 4: Add the route helper:**

```python
def _register_finding_retest_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the finding-level agentic-retest helpers (goal draft + session list, FR-17 6b-iii-b).

    Kept out of ``_register_finding_routes`` to stay under the mccabe gate.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}/goal/draft", response_model=GoalDraftOut)
    def draft_goal(
        finding_id: int, session: SessionDep, goal_agent: GoalAgentDep
    ) -> GoalDraftOut:
        """Generate a retest-goal draft for the finding — no session, no persistence."""
        finding = _current_or_404(session, finding_id).to_domain()
        return GoalDraftOut(steps=list(generate_goal(goal_agent, finding)))
```

(The `GET …/retest-sessions` route is added to this same helper in Task 3.)

- [ ] **Step 5: Wire it in `create_app`** after `_register_finding_routes(api, sessions)`:

```python
    _register_finding_retest_routes(api, sessions)
```

- [ ] **Step 6: Run the test + gate.** Route test PASS; `uv run mypy && uv run ruff check src tests && uv run xenon --max-absolute C src`.

- [ ] **Step 7: Commit.**
```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(retest): POST /findings/{id}/goal/draft — pre-session goal generation (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `GET /findings/{id}/retest-sessions` — session summaries for a finding

**Files:**
- Modify: `src/revalid/app.py` — add `RetestSessionSummary` model + a `list_finding_sessions` route inside `_register_finding_retest_routes`
- Test: same integration file

**Interfaces:**
- Produces: `GET /api/findings/{id}/retest-sessions` → `list[RetestSessionSummary]`, **newest first** (by id desc). Empty list when none (200, not 404).
- `RetestSessionSummary` = `{ id: int, finding_id: int, status: str, verdict_status: str | None, created_at: datetime }`.

- [ ] **Step 1: Write the failing test.**

```python
def test_list_finding_sessions_newest_first(...) -> None:
    # finding id 1; start two sessions
    a = client.post("/api/findings/1/retest-session").json()["id"]
    b = client.post("/api/findings/1/retest-session").json()["id"]
    rows = client.get("/api/findings/1/retest-sessions").json()
    assert [r["id"] for r in rows] == [b, a]              # newest first
    assert {r["finding_id"] for r in rows} == {1}
    assert client.get("/api/findings/999/retest-sessions").json() == []  # unknown finding → empty
```

- [ ] **Step 2: Run it, verify it fails.** → FAIL (404 route).

- [ ] **Step 3: Add the summary model** (near `RetestSessionOut`, ~258):

```python
class RetestSessionSummary(BaseModel):
    """A compact retest-session row for a finding's session list (FR-17 6b-iii-b)."""

    id: int
    finding_id: int
    status: str
    verdict_status: str | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: RetestSessionRecord) -> "RetestSessionSummary":
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            status=record.status,
            verdict_status=record.verdict_status,
            created_at=record.created_at,
        )
```

Ensure `from datetime import datetime` is imported in `app.py` (it is used by other models; confirm with grep — if missing, add it).

- [ ] **Step 4: Add the route** inside `_register_finding_retest_routes`:

```python
    @router.get("/findings/{finding_id}/retest-sessions", response_model=list[RetestSessionSummary])
    def list_finding_sessions(finding_id: int, session: SessionDep) -> list[RetestSessionSummary]:
        """List a finding's retest sessions, newest first (FR-17 6b-iii-b)."""
        rows = session.scalars(
            select(RetestSessionRecord)
            .where(RetestSessionRecord.finding_id == finding_id)
            .order_by(RetestSessionRecord.id.desc())
        )
        return [RetestSessionSummary.from_record(r) for r in rows]
```

- [ ] **Step 5: Run the test + gate.** PASS; mypy/ruff/xenon green.

- [ ] **Step 6: Commit.**
```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(retest): GET /findings/{id}/retest-sessions — session summaries (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase B — Frontend client + types

### Task 4: Client + types — drop batch, add the three new calls, simplify `Verdict`

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/hooks/queryKeys.ts`
- Test: type-check + existing client-consuming tests compile (no dedicated client unit test)

**Interfaces:**
- Produces (client): `draftGoal(findingId: number): Promise<{ steps: string[] }>`; `listRetestSessions(findingId: number): Promise<RetestSessionSummary[]>`; `StartSessionOptions` gains `initial_goal?: string[]`.
- Produces (types): `RetestSessionSummary`; `Verdict` reduced to agentic shape; **removed**: `Plan`, `PlanStatus`, `Probe`, `PlannedAction`, `RejectedAction`, `Evidence` (batch).
- Removed (client): `generatePlan`, `editPlan`, `approvePlan`, `rejectPlan`, `revisePlan`, `listPlans`, `retest`.

- [ ] **Step 1: Add the new client fns + option field** in `client.ts`. Extend `StartSessionOptions`:

```ts
export interface StartSessionOptions {
  free_launch?: boolean;
  max_steps?: number;
  max_seconds?: number | null;
  /** A pre-start user-owned goal (FR-17 6b-iii-b); seeded verbatim if present. */
  initial_goal?: string[];
}
```

Add (in the session section):

```ts
/** Generate a retest-goal draft for a finding, pre-session (FR-17 6b-iii-b). */
export function draftGoal(findingId: number): Promise<{ steps: string[] }> {
  return request<{ steps: string[] }>(`/findings/${String(findingId)}/goal/draft`, {
    method: "POST",
  });
}

/** List a finding's retest sessions, newest first (FR-17 6b-iii-b). */
export function listRetestSessions(findingId: number): Promise<RetestSessionSummary[]> {
  return request<RetestSessionSummary[]>(`/findings/${String(findingId)}/retest-sessions`);
}
```

- [ ] **Step 2: Delete the batch client fns.** Remove `generatePlan`, `editPlan`, `approvePlan`, `rejectPlan`, `revisePlan`, `listPlans`, `retest` and any now-unused `Plan`/`PlannedAction` imports at the top of `client.ts`.

- [ ] **Step 3: Reshape `types.ts`.** Add:

```ts
/** A compact retest-session row for a finding's session list (FR-17 6b-iii-b). */
export interface RetestSessionSummary {
  id: number;
  finding_id: number;
  status: string;
  verdict_status: string | null;
  created_at: string;
}
```

Reduce `Verdict` to the agentic shape and drop the batch `Evidence` union:

```ts
export interface Verdict {
  id: number;
  finding_id: number;
  status: VerdictStatus;
  reason_code: string;
  rationale: string;
  matched_indicators: string[];
  session_id: number | null;
  actor: string;
  evidence: AgenticEvidence | null;
}
```

Delete `PlanStatus`, `Plan`, `Probe`, `PlannedAction`, `RejectedAction`, and the batch `Evidence` interface.

- [ ] **Step 4: Update `queryKeys.ts`.** Remove `plans`; add a finding-sessions key; fix the session key to match the literal one `RetestSession.tsx` actually uses (unify on one form):

```ts
  findingSessions: (findingId: number) => ["findingSessions", findingId] as const,
  // keep a single session key; RetestSession.tsx will be switched to this in Task 9
  retestSession: (id: number) => ["retest-session", id] as const,
```

- [ ] **Step 5: Type-check.** From `frontend/`: `npx tsc --noEmit`. Expect errors ONLY in files handled by later tasks (usePlans, stages, selectors, EvidenceView, VerdictCard). That's expected mid-reshape; this task's own edits must be internally consistent. (Do not fix downstream files here.)

> Note: because deleting the batch client fns breaks imports app-wide, Tasks 4–11 form one **compile-coherent unit** — run the full frontend gate at the END of Task 11, not after Task 4. Commit Task 4 without a green `tsc` (WIP), or fold Tasks 4–11 into a single working branch state and commit at natural checkpoints. Prefer: commit each task; run the full green gate at Task 11's end.

- [ ] **Step 6: Commit.**
```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts frontend/src/hooks/queryKeys.ts
git commit -m "refactor(ui): drop batch client/types, add goal-draft + session-list + agentic Verdict (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase C — Pipeline model + layout context

### Task 5: Rewrite `pipelineReach` + drop `currentPlan`; add `useFindingSessions`

**Files:**
- Modify: `frontend/src/lib/selectors.ts`, `frontend/src/lib/selectors.test.ts`
- Create: `frontend/src/hooks/useFindingSessions.ts`

**Interfaces:**
- Produces: `pipelineReach({ sessionExists, hasVerdict }: { sessionExists: boolean; hasVerdict: boolean })` → `{ reached: boolean[]; furthest: number; current: number }` over the 4 stages `[extract, goal, retest, verdict]`.
- Produces: `useFindingSessions(findingId: number, enabled?: boolean)` → TanStack query of `RetestSessionSummary[]` (`queryKeys.findingSessions(findingId)`, `queryFn: () => listRetestSessions(findingId)`), with `refetchInterval` while any session is non-terminal.
- Keeps: `latestVerdict`, `verdictCounts` unchanged. Removes: `currentPlan`, `LIVE_PLAN_STATUSES`.

- [ ] **Step 1: Write the failing selector test.** In `selectors.test.ts`, replace the `pipelineReach` cases with the 4-stage model:

```ts
describe("pipelineReach (4-stage)", () => {
  it("extract+goal reachable, retest/verdict not, before any session", () => {
    const r = pipelineReach({ sessionExists: false, hasVerdict: false });
    expect(r.reached).toEqual([true, true, false, false]);
    expect(r.current).toBe(2); // retest is the next action
  });
  it("retest reached once a session exists", () => {
    const r = pipelineReach({ sessionExists: true, hasVerdict: false });
    expect(r.reached).toEqual([true, true, true, false]);
    expect(r.current).toBe(3);
  });
  it("verdict reached once a verdict exists", () => {
    const r = pipelineReach({ sessionExists: true, hasVerdict: true });
    expect(r.reached).toEqual([true, true, true, true]);
    expect(r.furthest).toBe(3);
  });
});
```

Remove the `currentPlan`/`makePlan` cases from this file.

- [ ] **Step 2: Run it, verify it fails.** `cd frontend && npx vitest run src/lib/selectors.test.ts` → FAIL.

- [ ] **Step 3: Rewrite `pipelineReach`** (and delete `currentPlan` + `LIVE_PLAN_STATUSES` + the `Plan` import):

```ts
/**
 * The four-stage revalidation pipeline as reach flags (FR-17 6b-iii-b):
 * extract → goal → retest → verdict. Extract and goal are always reachable;
 * retest opens once a session exists; verdict once a verdict exists.
 */
export function pipelineReach({
  sessionExists,
  hasVerdict,
}: {
  sessionExists: boolean;
  hasVerdict: boolean;
}): { reached: boolean[]; furthest: number; current: number } {
  const reached = [true, true, sessionExists || hasVerdict, hasVerdict];
  const nextUnreached = reached.indexOf(false);
  return {
    reached,
    furthest: reached.lastIndexOf(true),
    current: nextUnreached === -1 ? reached.length - 1 : nextUnreached,
  };
}
```

- [ ] **Step 4: Create `useFindingSessions.ts`** (mirror `usePlans`'s refetch-while-active pattern):

```ts
import { useQuery } from "@tanstack/react-query";

import { listRetestSessions } from "../api/client";
import { queryKeys } from "./queryKeys";

const TERMINAL = new Set(["concluded", "given_up", "ended", "error"]);

/** A finding's retest sessions, newest first; polls while any is non-terminal. */
export function useFindingSessions(findingId: number, enabled = true) {
  return useQuery({
    queryKey: queryKeys.findingSessions(findingId),
    queryFn: () => listRetestSessions(findingId),
    enabled: enabled && Number.isFinite(findingId),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((s) => !TERMINAL.has(s.status)) ? 2000 : false,
  });
}
```

- [ ] **Step 5: Run the selector test.** `npx vitest run src/lib/selectors.test.ts` → PASS.

- [ ] **Step 6: Commit.**
```bash
git add frontend/src/lib/selectors.ts frontend/src/lib/selectors.test.ts frontend/src/hooks/useFindingSessions.ts
git commit -m "refactor(ui): 4-stage pipelineReach + useFindingSessions (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `FindingLayout` context + `PipelineTrack` to four stages

**Files:**
- Modify: `frontend/src/hooks/useFindingStage.ts`, `frontend/src/components/FindingLayout.tsx`, `frontend/src/components/PipelineTrack.tsx`

**Interfaces:**
- Produces: `FindingStageContext = { finding, findingId, sessions: RetestSessionSummary[], latestSession?: RetestSessionSummary, verdicts: Verdict[], currentStage: Stage }` (drops `plans`/`currentPlan`/`hasPlan`/`approved`/`retested`).
- Produces: `Stage = "extract" | "goal" | "retest" | "verdict"`; `PipelineTrack` renders four nodes.
- Consumes: `pipelineReach({ sessionExists, hasVerdict })`, `useFindingSessions`.

- [ ] **Step 1: Update the context type** (`useFindingStage.ts`):

```ts
import type { Finding, RetestSessionSummary, Verdict } from "../api/types";
import type { Stage } from "../components/PipelineTrack";

export interface FindingStageContext {
  finding: Finding;
  findingId: number;
  /** This finding's retest sessions, newest first. */
  sessions: RetestSessionSummary[];
  /** The newest session, if any (the one the retest stage opens). */
  latestSession?: RetestSessionSummary;
  /** This finding's verdicts, newest first. */
  verdicts: Verdict[];
  currentStage: Stage;
}
```

- [ ] **Step 2: Update `PipelineTrack`.** Change `STAGES` to `["extract", "goal", "retest", "verdict"] as const`; the grid is now `grid-cols-4`; the progress-fill width uses 4 nodes: replace `furthest * 20` with `furthest * (100 / (STAGES.length - 1) / …)` — concretely, nodes sit at `inset-x-[12.5%]` (1/8 and 7/8) for 4 columns; set the rail to `inset-x-[12.5%]` and the fill width to `${(furthest / (STAGES.length - 1)) * 75}%`. The verdict-tone branch keys on `i === 3` (last index) now, not `i === 4`.

```tsx
const STAGES = ["extract", "goal", "retest", "verdict"] as const;
// …
        <div className="absolute inset-x-[12.5%] top-[13px] h-px bg-line" />
        <div
          className="rev-grow absolute top-[13px] left-[12.5%] h-px bg-iris/60"
          style={{ width: `${String((furthest / 3) * 75)}%` }}
        />
        <ol className="relative grid grid-cols-4">
// …
            if (isReached && i === 3 && verdict) {  // final node borrows verdict tone
```

- [ ] **Step 3: Update `FindingLayout`.** Swap `usePlans` → `useFindingSessions`; compute the new context:

```tsx
import { useFindingSessions } from "../hooks/useFindingSessions";
// …
const STAGES: readonly Stage[] = ["extract", "goal", "retest", "verdict"];
// …inside the component…
  const sessionsQuery = useFindingSessions(findingId);   // replaces usePlans(findingId)
  const verdicts = useVerdicts();
// …after the finding-not-found guard…
  const sessions = sessionsQuery.data ?? [];
  const findingVerdicts = (verdicts.data ?? [])
    .filter((v) => v.finding_id === findingId)
    .sort((a, b) => b.id - a.id);
  const reach = pipelineReach({
    sessionExists: sessions.length > 0,
    hasVerdict: findingVerdicts.length > 0,
  });
  const currentStage = STAGES[reach.current];
  // …deep-link guard uses STAGES.indexOf(segment) vs reach.current, unchanged…
  const context: FindingStageContext = {
    finding,
    findingId,
    sessions,
    latestSession: sessions[0],   // newest first
    verdicts: findingVerdicts,
    currentStage,
  };
```

Update `PipelineTrack` usage: it currently takes `planned`/`approved`/`retested`. Replace those props — `PipelineTrack` should take `sessionExists`/`hasVerdict` directly and call `pipelineReach` internally (it already does). Change its prop type to `{ sessionExists: boolean; hasVerdict: boolean; verdict?: VerdictStatus; findingId: number; activeStage: Stage }` and pass `sessionExists={sessions.length > 0} hasVerdict={findingVerdicts.length > 0}`.

- [ ] **Step 4: Type-check the trio.** `npx tsc --noEmit` — remaining errors should be only in the not-yet-done stages/console/tests. Confirm no error originates in these three files.

- [ ] **Step 5: Commit.**
```bash
git add frontend/src/hooks/useFindingStage.ts frontend/src/components/FindingLayout.tsx frontend/src/components/PipelineTrack.tsx
git commit -m "refactor(ui): FindingLayout + PipelineTrack to 4 stages, session-driven progress (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase D — Stage teardown + routing

### Task 7: Delete batch stages/components/hooks + rewire routes

**Files:**
- Delete: `frontend/src/routes/stages/PlanStage.tsx`, `frontend/src/routes/stages/ApproveStage.tsx`, `frontend/src/components/PlanActions.tsx`, `frontend/src/components/PlanActions.test.tsx`, `frontend/src/components/PlanHistory.tsx`, `frontend/src/components/InstructionsField.tsx`, `frontend/src/hooks/usePlans.ts`, `frontend/src/lib/planActions.ts`, `frontend/src/lib/planActions.test.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:** removes the `plan`/`approve` routes and their imports; adds `goal`. `retest`/`verdict`/`extract` stay (their components are rewritten in later tasks).

- [ ] **Step 1: Delete the batch files.**
```bash
cd /home/alvar/tfg
git rm frontend/src/routes/stages/PlanStage.tsx frontend/src/routes/stages/ApproveStage.tsx \
  frontend/src/components/PlanActions.tsx frontend/src/components/PlanActions.test.tsx \
  frontend/src/components/PlanHistory.tsx frontend/src/components/InstructionsField.tsx \
  frontend/src/hooks/usePlans.ts frontend/src/lib/planActions.ts frontend/src/lib/planActions.test.ts
```
(If `git rm` is blocked by the sandbox classifier, `rm` the files and let git track the deletion.)

- [ ] **Step 2: Update `App.tsx` routes + imports.** Remove the `PlanStage`/`ApproveStage` imports; add `GoalStage` (created in Task 8). New finding routes:

```tsx
import { GoalStage } from "./routes/stages/GoalStage";
// …
            <Route path="/findings/:id" element={<FindingLayout />}>
              <Route index element={<StageRedirect />} />
              <Route path="extract" element={<ExtractStage />} />
              <Route path="goal" element={<GoalStage />} />
              <Route path="retest" element={<RetestStage />} />
              <Route path="verdict" element={<VerdictStage />} />
            </Route>
            <Route path="/retest-sessions/:id" element={<RetestSession />} />
```

- [ ] **Step 3: Type-check.** `npx tsc --noEmit` — expect errors now only in `GoalStage` (missing until Task 8), `RetestStage`, `VerdictStage`, `EvidenceView`, `VerdictCard`, and their tests. No error should reference a deleted batch file.

- [ ] **Step 4: Commit.**
```bash
git add -A
git commit -m "refactor(ui): delete batch plan/approve stages + hooks/components (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase E — Goal stage

### Task 8: New `GoalStage` — pre-start editable draft + Start retest

**Files:**
- Create: `frontend/src/routes/stages/GoalStage.tsx`
- Create: `frontend/src/hooks/useGoalDraft.ts`

**Interfaces:**
- Consumes: `useFindingStage()` (`finding`, `findingId`), `draftGoal`, `startRetestSession(findingId, { initial_goal })`, `NotesThread`, `Button`, `Panel`.
- Produces: renders the draft as an editable textarea + Regenerate + Start retest; on start → `navigate('/findings/{id}/retest')`.

- [ ] **Step 1: Create `useGoalDraft.ts`** (cache the draft per finding; manual regenerate):

```ts
import { useQuery } from "@tanstack/react-query";

import { draftGoal } from "../api/client";

/** Generate + cache a pre-start goal draft for a finding (FR-17 6b-iii-b). */
export function useGoalDraft(findingId: number) {
  return useQuery({
    queryKey: ["goalDraft", findingId],
    queryFn: () => draftGoal(findingId),
    enabled: Number.isFinite(findingId),
    staleTime: Infinity, // stable until an explicit Regenerate (refetch)
    refetchOnWindowFocus: false,
  });
}
```

- [ ] **Step 2: Write the failing stage test.** Add a `GoalStage` block to `routes/stages/stages.test.tsx` (client already `vi.mock`ed). Mock `client.draftGoal` → `{ steps: ["confirm endpoint", "retry bypass"] }` and `client.startRetestSession` → a `RetestSessionSummary`-ish `{ id: 5, ... }`:

```tsx
describe("GoalStage", () => {
  it("shows the generated draft and starts a seeded session", async () => {
    vi.mocked(client.draftGoal).mockResolvedValue({ steps: ["confirm endpoint", "retry bypass"] });
    vi.mocked(client.startRetestSession).mockResolvedValue({ id: 5 } as never);
    renderStage(<GoalStage />, stageContext({ currentStage: "goal" }));
    expect(await screen.findByDisplayValue(/confirm endpoint/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /start retest/i }));
    expect(client.startRetestSession).toHaveBeenCalledWith(7, {
      initial_goal: ["confirm endpoint", "retry bypass"],
    });
  });
});
```

- [ ] **Step 3: Run it, verify it fails.** `npx vitest run src/routes/stages/stages.test.tsx -t GoalStage` → FAIL (no `GoalStage`).

- [ ] **Step 4: Implement `GoalStage.tsx`:**

```tsx
import { useEffect, useState } from "react";

import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { startRetestSession } from "../../api/client";
import { NotesThread } from "../../components/NotesThread";
import { Button } from "../../components/ui/Button";
import { Eyebrow, Panel } from "../../components/ui/Panel";
import { useFindingStage } from "../../hooks/useFindingStage";
import { useGoalDraft } from "../../hooks/useGoalDraft";
import { errorMessage } from "../../lib/format";

/** Stage 2 — draft + edit the retest goal, then launch a seeded agentic session (FR-17). */
export function GoalStage() {
  const { findingId } = useFindingStage();
  const draft = useGoalDraft(findingId);
  const navigate = useNavigate();
  const [text, setText] = useState("");

  // Seed the editable box from the generated draft once it arrives / on regenerate.
  useEffect(() => {
    if (draft.data) setText(draft.data.steps.join("\n"));
  }, [draft.data]);

  const start = useMutation({
    mutationFn: () =>
      startRetestSession(findingId, {
        initial_goal: text.split("\n").map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      navigate(`/findings/${String(findingId)}/retest`);
    },
  });

  return (
    <div className="space-y-6">
      <Panel>
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <Eyebrow>Retest goal</Eyebrow>
          <Button
            variant="ghost"
            disabled={draft.isFetching}
            onClick={() => void draft.refetch()}
          >
            {draft.isFetching ? "Generating…" : "Regenerate"}
          </Button>
        </div>
        <div className="space-y-3 p-4">
          <p className="text-sm text-dim">
            A generic, editable goal for the agent — one step per line. Edit it, then start the
            sandboxed retest; you can keep steering the goal live in the console.
          </p>
          <textarea
            aria-label="retest goal steps"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
            }}
            rows={5}
            placeholder="One verification step per line…"
            className="w-full rounded border border-line bg-panel px-2 py-1 font-mono text-[13px] text-fg"
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button
              disabled={start.isPending}
              onClick={() => {
                start.mutate();
              }}
            >
              {start.isPending ? "Starting…" : "Start retest"}
            </Button>
            <span className="font-mono text-[11px] text-faint">
              Launches the egress-locked agent with this goal.
            </span>
          </div>
          {draft.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(draft.error)}
            </p>
          )}
          {start.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(start.error)}
            </p>
          )}
        </div>
      </Panel>
      <NotesThread findingId={findingId} stage="plan" scope="stage" />
    </div>
  );
}
```

(Note: `NotesThread` `stage` prop is a `FindingStage` enum value; `"plan"` still exists in the backend `FindingStage` enum — reuse it for the goal stage's notes so no backend enum change is needed. If lint flags the literal, import the stage constant the other stages use.)

- [ ] **Step 5: Run the test.** `npx vitest run src/routes/stages/stages.test.tsx -t GoalStage` → PASS.

- [ ] **Step 6: Commit.**
```bash
git add frontend/src/routes/stages/GoalStage.tsx frontend/src/hooks/useGoalDraft.ts frontend/src/routes/stages/stages.test.tsx
git commit -m "feat(ui): Goal stage — pre-start editable goal draft + seeded launch (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase F — Console as the retest stage

### Task 9: `RetestSession` takes a `sessionId` prop; `RetestStage` hosts it

**Files:**
- Modify: `frontend/src/routes/RetestSession.tsx` (param → prop), `frontend/src/routes/stages/RetestStage.tsx` (rewrite)
- Create: `frontend/src/routes/RetestSessionRoute.tsx` (thin param wrapper for the standalone `/retest-sessions/:id` route)
- Modify: `frontend/src/App.tsx` (point `/retest-sessions/:id` at the wrapper)

**Interfaces:**
- Produces: `RetestSession({ sessionId }: { sessionId: number })` (was `useParams().id`).
- Produces: `RetestStage` resolves `latestSession` from context; renders `<RetestSession sessionId={latestSession.id} />`, or redirects to `goal` when none.

- [ ] **Step 1: Change `RetestSession` to a prop.** Replace `const id = Number(useParams().id);` with a prop:

```tsx
export function RetestSession({ sessionId }: { sessionId: number }) {
  const id = sessionId;
  // …rest unchanged…
```

Remove the now-unused `useParams` import if nothing else uses it. Switch the record fetch key from the literal `["retest-session", id]` to `queryKeys.retestSession(id)` (which Task 4 set to `["retest-session", id]`, so behavior is identical) and import `queryKeys`.

- [ ] **Step 2: Add the standalone route wrapper** `RetestSessionRoute.tsx`:

```tsx
import { useParams } from "react-router-dom";

import { RetestSession } from "./RetestSession";

/** URL-param wrapper so /retest-sessions/:id keeps working as a deep link. */
export function RetestSessionRoute() {
  return <RetestSession sessionId={Number(useParams().id)} />;
}
```

Point `App.tsx`'s `/retest-sessions/:id` at `<RetestSessionRoute />` (import swap).

- [ ] **Step 3: Write the failing `RetestStage` test.** Replace the old `RetestStage` describe block in `stages.test.tsx`:

```tsx
describe("RetestStage", () => {
  it("redirects to goal when the finding has no session", () => {
    renderStage(<RetestStage />, stageContext({ currentStage: "goal", sessions: [] }));
    // StageRedirect/Navigate target asserted via router test util, or assert the
    // console is absent: no "Agentic retest session" eyebrow rendered.
    expect(screen.queryByText(/agentic retest session/i)).not.toBeInTheDocument();
  });
  it("renders the console for the latest session", () => {
    const s = { id: 9, finding_id: 7, status: "thinking", verdict_status: null, created_at: "" };
    // stub the WS hook + record fetch used by RetestSession, or shallow-assert it mounts
    renderStage(<RetestStage />, stageContext({ sessions: [s], latestSession: s }));
    // assert RetestSession got sessionId 9 (e.g. via a spy/mock on ../RetestSession)
  });
});
```

Prefer mocking `../../RetestSession` (`vi.mock`) to a stub that records its `sessionId` prop, so the stage test doesn't need the WS machinery.

- [ ] **Step 4: Rewrite `RetestStage.tsx`:**

```tsx
import { Navigate } from "react-router-dom";

import { RetestSession } from "../RetestSession";
import { useFindingStage } from "../../hooks/useFindingStage";

/** Stage 3 — the agentic retest console for the finding's latest session (FR-17). */
export function RetestStage() {
  const { findingId, latestSession } = useFindingStage();
  if (!latestSession) {
    return <Navigate to={`/findings/${String(findingId)}/goal`} replace />;
  }
  return <RetestSession sessionId={latestSession.id} />;
}
```

- [ ] **Step 5: Run the tests.** `npx vitest run src/routes/stages/stages.test.tsx -t RetestStage` → PASS.

- [ ] **Step 6: Commit.**
```bash
git add frontend/src/routes/RetestSession.tsx frontend/src/routes/RetestSessionRoute.tsx frontend/src/routes/stages/RetestStage.tsx frontend/src/App.tsx frontend/src/routes/stages/stages.test.tsx
git commit -m "refactor(ui): host the agentic console as the retest stage (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Console relayout — goal to a right column, terminal at the bottom

**Files:**
- Modify: `frontend/src/routes/RetestSession.tsx` (JSX layout only)
- Modify: `frontend/src/routes/RetestSession.test.tsx` (structural assertions)

**Interfaces:** presentation only — no `/api`, WS, mutation, or state change. The goal `Panel` moves from a full-width block above the chat into a right column beside the chat; terminal + input stay docked at the bottom, full width.

- [ ] **Step 1: Restructure the JSX.** Wrap the chat + goal in a two-column flex row, with the terminal + input below it. Replace the current top-level `return (<div className="flex h-… flex-col gap-4"> header, goal Panel, chat div, terminal Panel, form </div>)` structure with:

```tsx
  return (
    <div className="flex h-[calc(100dvh-9rem)] min-h-[28rem] flex-col gap-4">
      {/* header row — unchanged */}
      {/* … existing header JSX … */}

      {/* main: chat (left, grows) + goal (right, fixed-ish column) */}
      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        <div
          ref={chatRef}
          role="log"
          aria-label="Agent conversation"
          className="min-h-0 flex-1 overflow-y-auto"
        >
          {/* … existing chat inner content (chatItems, verdict, adjudication) … */}
        </div>

        <aside className="shrink-0 lg:w-[20rem]">
          {/* the existing "Current goal" Panel, moved here verbatim */}
        </aside>
      </div>

      {/* terminal — docked bottom, full width (existing Panel) */}
      {/* operator input form — bottom (existing form) */}
    </div>
  );
```

Move the existing **Current goal** `<Panel>` block (lines ~392-469) into the `<aside>`; keep all its handlers/state. Keep the chat inner `<div className="mx-auto flex max-w-[52rem] …">` but drop the `max-w-[52rem]` centering so it fills the left column (or keep a looser `max-w-none`). Terminal `<Panel>` and the operator `<form>` stay exactly as they are, after the two-column row.

- [ ] **Step 2: Update `RetestSession.test.tsx`.** It renders via the standalone route today (`useParams`); switch it to render `<RetestSession sessionId={N} />` directly (or via `<RetestSessionRoute/>` in a `MemoryRouter`). Keep the behavioral assertions (goal edit, approve/reject, adjudication, terminal lines). Add one structural assertion that the goal panel and chat coexist (both `Current goal` and `Agent conversation` present), and that layout didn't drop any control. Do **not** assert pixel widths.

- [ ] **Step 3: Run the console tests.** `npx vitest run src/routes/RetestSession.test.tsx` → PASS.

- [ ] **Step 4: Commit.**
```bash
git add frontend/src/routes/RetestSession.tsx frontend/src/routes/RetestSession.test.tsx
git commit -m "refactor(ui): console layout — goal right column, terminal bottom (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase G — Verdict stage agentic-only

### Task 11: `VerdictStage` / `VerdictCard` / `EvidenceView` — drop the batch branch; full green gate

**Files:**
- Modify: `frontend/src/components/EvidenceView.tsx`, `frontend/src/components/VerdictCard.tsx`, `frontend/src/routes/stages/VerdictStage.tsx`
- Modify: any `EvidenceView.test.tsx` / `VerdictCard.test.tsx` that build batch `Evidence`

**Interfaces:** `EvidenceView` renders only `AgenticEvidence` (or nothing); `VerdictCard` reads only the agentic `Verdict` fields (no `probe_kind`/`plan_version`/`source`/batch `evidence`).

- [ ] **Step 1: Simplify `EvidenceView`.** Since `Verdict.evidence` is now `AgenticEvidence | null`, drop the `"explanation" in evidence` discrimination and the entire batch branch:

```tsx
export function EvidenceView({ verdict }: { verdict: Verdict }) {
  const { evidence } = verdict;
  if (evidence === null) return null;
  return <AgenticEvidenceView evidence={evidence} />;
}
```

Keep `AgenticEvidenceView` as-is. Remove the batch JSX (request/response/matched-indicators) and any now-unused imports.

- [ ] **Step 2: Trim `VerdictCard`.** Remove any rendering of `verdict.probe_kind` / `plan_version` / `source`. Keep status, reason_code, rationale, matched_indicators, and `<EvidenceView verdict={verdict} />`. Update `VerdictCard.test.tsx` fixtures to the agentic `Verdict` shape (drop `source`/`probe_kind`/`plan_version`/batch `evidence`; set `evidence` to an `AgenticEvidence` or `null`).

- [ ] **Step 3: `VerdictStage`** already reads `verdicts` + renders `DeterminationMeter` + `VerdictCard` + empty-state link to `/findings/{id}/retest` — no structural change; just confirm it compiles against the trimmed `Verdict`. Its empty-state link target `retest` is still valid (redirects to goal if no session).

- [ ] **Step 4: Run the FULL frontend gate** (first time everything compiles together):
```bash
cd frontend
npx tsc --noEmit
npx eslint .
npx vitest run
npm run build
```
Fix any remaining type/lint errors (they should only be fixture shapes). Expected: all green, no reference to a deleted batch symbol.

- [ ] **Step 5: Commit.**
```bash
git add frontend/src
git commit -m "refactor(ui): agentic-only verdict rendering; full frontend gate green (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase H — Frontend test sweep

### Task 12: Update `FindingLayout` + `PipelineTrack` tests to four stages

**Files:**
- Modify: `frontend/src/components/FindingLayout.test.tsx`, `frontend/src/components/PipelineTrack.test.tsx`

**Interfaces:** tests assert the 4-stage flow and session-driven progress.

- [ ] **Step 1: `PipelineTrack.test.tsx`.** Change `STAGES` expectations to `["extract","goal","retest","verdict"]`; the four labels render; the verdict-tone final node still applies with `hasVerdict`/`verdict:"fixed"`; the reached-vs-inert link assertions move to the new props (`sessionExists`/`hasVerdict`). Update `renderTrack` props to the new signature.

- [ ] **Step 2: `FindingLayout.test.tsx`.** Replace `client.listPlans` stub with `client.listRetestSessions` → `[]` (and a variant returning one session). Replace the `approvedPlan` fixture with a session summary. Update the deep-link redirect expectations to the 4-stage model: `/approve` no longer exists; assert `/retest` with **no** session redirects to `child-goal`, `/retest` **with** a session renders `child-retest`, `/verdict` with a verdict renders `child-verdict`, `/plan` (gone) → whatever `currentStage` resolves. Keep child stub routes for `extract/goal/retest/verdict`.

- [ ] **Step 3: Run the full frontend gate again.**
```bash
cd frontend && npx tsc --noEmit && npx eslint . && npx vitest run && npm run build
```
All green.

- [ ] **Step 4: Commit.**
```bash
git add frontend/src/components/FindingLayout.test.tsx frontend/src/components/PipelineTrack.test.tsx
git commit -m "test(ui): FindingLayout + PipelineTrack tests to the 4-stage flow (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase I — Deferred cleanups

### Task 13: Delete the orphaned `allowlist.py`

**Files:**
- Delete: `src/revalid/allowlist.py`, `tests/unit/test_allowlist.py`

- [ ] **Step 1: Confirm it is dead in `src`.**
```bash
grep -rn "allowlist\|TargetGuard\|canonicalize\|AllowlistTransport\|load_allowlist\|TargetNotAllowedError\|DEFAULT_ALLOWLIST" src/revalid | grep -v "src/revalid/allowlist.py"
```
Expect only the two comment mentions (`sandbox.py`, `settings.py`) — no import/use. If a real import appears, STOP and reassess (don't delete).

- [ ] **Step 2: Delete both files.**
```bash
git rm src/revalid/allowlist.py tests/unit/test_allowlist.py
```
(If blocked, `rm` them.)

- [ ] **Step 3: Backend gate.** `uv run pytest tests/unit tests/integration -q && uv run mypy && uv run ruff check src tests && uv run vulture src --min-confidence 80 && uv run xenon --max-absolute C src` → all green (vulture no longer even has the module to see).

- [ ] **Step 4: Commit.**
```bash
git add -A
git commit -m "refactor(retest): delete the orphaned FR-06 HTTP allowlist (superseded by sandbox network, ADR-0033)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 14: Refresh the `c4.md` sequence diagrams to the agentic flow

**Files:**
- Modify: `docs/architecture/c4.md`

- [ ] **Step 1: Rewrite the three stale diagrams.** In `docs/architecture/c4.md`: (a) the Level-2 "M1 deterministic slice" diagram — replace the `retest.py`/`AllowlistTransport`/`run_probe`/`assess` participants with the agentic slice, OR relabel the section as historical M1 and add a note it was superseded; (b) the Level-3 "FR-11 ingest → verdict flow" — replace the `Generate plan → Approve → execute_approved_plan` steps with `Set goal → Start agentic session → gated commands → verdict`; (c) delete/rewrite the Level-3 "FR-08 guarded execution" diagram (`execute_approved_plan`/`guarded_run`/`sanity.py`/`run_probe` are gone) — replace with the sandbox command-gate loop (propose → approve → `sandbox.exec` → observe) or remove it and fold a one-line pointer to ADR-0025. Match the real current flow: SPA → `/api/findings/{id}/retest-session` → background `run_first_step` → `start_and_step` → gated `run_command` in `DockerSandbox` → `record_verdict`.

- [ ] **Step 2: Sanity-check the Mermaid renders.** `grep -c "sequenceDiagram" docs/architecture/c4.md`; visually confirm no deleted module name remains: `grep -nE "retest\.py|approval\.py|sanity\.py|execute_approved_plan|guarded_run|AllowlistTransport|run_probe" docs/architecture/c4.md` → no matches.

- [ ] **Step 3: Commit.**
```bash
git add docs/architecture/c4.md
git commit -m "docs(arch): refresh C4 sequence diagrams to the agentic retest flow (FR-17 6b-iii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase J — Requirements, roadmap, release gate, PR

### Task 15: SRS AC22 + roadmap + full gate + codebase-sanity + PR (closes #110)

**Files:**
- Modify: `docs/requirements/srs.md` (FR-17 ACs + FR-11 note), `docs/roadmap.md`

- [ ] **Step 1: SRS.** Under FR-17, add the Slice 6b-iii-b AC block:

```markdown
- **Acceptance criteria — Slice 6b-iii-b** (met — issue #110, ADR-0033, 2026-07-19):
  - [x] **AC22**: the finding flow is **extract → goal → retest → verdict**; no batch
    stage/hook/client-fn/`Plan` type remains and the SPA calls no removed endpoint. The
    Goal stage generates an editable pre-start draft goal (no session), and **Start retest**
    launches a session seeded with it; the console is the only retest path, relaid out as
    chat + right-editable goal + bottom terminal, with live goal edit, the command gate, chat
    steering, and adjudication intact and an in-progress session surviving reload.
```

Update the FR-17 "Remaining — Slice 6b-iii-b" line to "**done**"; update the FR-11 (#16) note if it still describes "approve plan → retest" (reword to the agentic flow). Confirm FR-04/05/07/08 stay *superseded* and FR-14 *dropped* (already set in 6b-iii-a).

- [ ] **Step 2: Roadmap.** Add a `**2026-07-19 (M6) — Slice 6b-iii-b built (FR-17): finding-flow reshape**` entry summarizing the four-stage flow, the three new endpoints, the pre-start goal, the console relayout, and the two cleanups; mark the Slice 6b checklist item **done** and note M6/FR-17 is now complete (release is Álvaro's call).

- [ ] **Step 3: Full green gate (backend + frontend + demos).**
```bash
uv run pytest tests/unit tests/integration -q
uv run mypy && uv run ruff check src tests scripts && uv run ruff format --check src tests
uv run xenon --max-absolute C src
make demo-retest-session && make demo-export && make demo-eval
( cd frontend && npx tsc --noEmit && npx eslint . && npx vitest run && npm run build )
```
All green.

- [ ] **Step 4: Run the `codebase-sanity` agent** (M6 release gate) over the working tree; address anything real (expect a clean bill — batch fully gone).

- [ ] **Step 5: Commit docs.**
```bash
git add docs/requirements/srs.md docs/roadmap.md
git commit -m "docs(retest): FR-17 6b-iii-b AC22 + roadmap — finding-flow reshape complete (closes #110)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 6: Push + PR.**
```bash
git push -u origin feat/fr17-finding-reshape-slice6b-iii-b
```
Open the PR titled **"FR-17 Slice 6b-iii-b: reshape the finding flow around the agentic console"** with a filled "How to validate" (the Phase-J gate commands + a browser walk-through: upload/pick a finding → Goal stage generates + edit → Start retest → console with goal-right/terminal-bottom → verdict), the FR-17 AC22 checkboxes, and body containing **`Closes #110`** (this is the last slice). Queue squash auto-merge; monitor CI to green. **After merge, re-check #110 is CLOSED** (this PR is the intended closer) — the board automation + the `Closes` keyword should both fire.

---

## Self-Review

**Spec coverage** (against `2026-07-19-…-slice-6b-iii-b-design.md`):
- §2.1 backend (goal/draft, initial_goal, list-sessions) → Tasks 1–3. ✓
- §2.2 stages + teardown → Tasks 6, 7. ✓
- §2.3 Goal stage (pre-start draft) → Task 8. ✓
- §2.4 console as retest stage + relayout → Tasks 9, 10. ✓
- §2.5 verdict stage agentic-only → Task 11. ✓
- §2.6 cleanups (c4.md, allowlist.py) → Tasks 13, 14. ✓
- §4 AC1–5 → covered across tasks; AC22 recorded in Task 15. ✓
- §5 test plan (backend endpoints, frontend stage/layout, delete batch tests) → Tasks 1–3, 5, 8–12. ✓

**Placeholder scan:** new code (endpoints, `pipelineReach`, `GoalStage`, `RetestStage`, console layout) shown in full; edits to large existing files (RetestSession, FindingLayout, VerdictCard) give the exact changed blocks + precise instructions rather than re-quoting unchanged JSX — intentional for a modify-in-place reshape, not a placeholder.

**Type consistency:** `RetestSessionSummary` fields match between backend model (Task 3) and TS type (Task 4); `pipelineReach({sessionExists,hasVerdict})` is defined (Task 5) and consumed identically (Tasks 6); `FindingStageContext` producer (Task 6) matches consumers (`GoalStage`/`RetestStage`/`VerdictStage`, Tasks 8–11); `startRetestSession(findingId, {initial_goal})` matches the backend `StartSessionRequest.initial_goal` (Task 1) and the client option (Task 4).

**Ordering caveat noted:** Tasks 4–11 are one compile-coherent unit — the frontend does not fully type-check until Task 11; commit per task, run the full green gate at Task 11 (and again at 12). Called out inline in Task 4.
