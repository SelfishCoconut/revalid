# FR-17 Slice 6b-ii-b — The user-owned goal (FR-04 repurposed)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every code task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The guiding plan becomes a user-owned goal: a generic `generate_goal` seeds it at session start (shown in the "Current goal" panel, given to the agent), and the user edits or regenerates it live — the change reaches the agent on its next turn (pure-queue), never interrupting a run.

**Architecture:** A new tool-agnostic `generate_goal(finding) → steps` (separate from `generate_plan`) runs in `run_first_step`'s background task, emits an initial `plan_updated` event, and prepends the goal to the retest agent's prompt. Live edits go through `POST …/goal` (+ `/regenerate`), which append a `plan_updated` event and queue the change on `LiveSession.pending_goal`; `_resume_with_decision` drains it into the next `user_prompt` — the same mechanism operator chat messages already use. Builds on 6b-ii-a (the agent's `set_plan` is already gone).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic AI (FunctionModel in tests), pytest; React/TS/Vite, vitest.

## Global Constraints

- Python 3.12+, `uv`; `mypy` (bare `uv run mypy` = CI), ruff (line 100, Google docstrings), xenon max absolute **C**.
- Coverage ≥ 80% on `src/`; Conventional Commits + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Frontend gates: eslint + tsc + build + vitest green; owned pure logic pinned.
- Best-effort goal generation MUST NOT block or fail session start (degrade to an empty goal).
- Command gating, free-launch, egress lock (NFR-03), single-user model (ADR-0008) unchanged.
- **Write-hook strips unused imports:** when adding an import, add its usage in the same edit or re-add the import after the usage exists (verify with grep).
- Branch: `feat/fr17-user-goal-slice6b-ii` (shared with 6b-ii-a, already pushed). PR body: **`Closes #107`** (this completes 6b-ii). ⚠️ Do NOT write the literal `closes #107` anywhere else in the body.

---

## File Structure

- `src/revalid/plan.py` — add `GeneratedGoal`, `build_goal_agent`, `generate_goal`.
- `src/revalid/retest_session.py` — add `LiveSession.pending_goal` + `set_pending_goal`/`drain_goal`; `set_goal(...)`; drain the goal in `_resume_with_decision`.
- `src/revalid/app.py` — `get_goal_agent`/`GoalAgentDep`; `_goal_prompt`/seed in `run_first_step`; wire `start_retest_session`; `GoalRequest`; `run_goal`/`run_regenerate_goal`; `_register_goal_routes`.
- `frontend/src/api/client.ts` — `setSessionGoal`, `regenerateSessionGoal`.
- `frontend/src/routes/RetestSession.tsx` (+ `.test.tsx`) — "Current goal" panel with Edit + Regenerate.
- `docs/requirements/srs.md`, `docs/roadmap.md`.

---

## Task 1: Generic goal generation

**Files:** Modify `src/revalid/plan.py`; Test `tests/unit/test_plan.py` (append).

**Interfaces:**
- Produces: `GeneratedGoal(steps: tuple[str, ...])`; `build_goal_agent(model=None) -> Agent[None, GeneratedGoal]`; `generate_goal(agent, finding) -> tuple[str, ...]` (empty tuple on model failure).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_plan.py` (check its existing imports; it already imports `build_*`/`generate_*` + `Finding`/`Severity` + `FunctionModel`):

```python
def _goal_model(steps: list[str]):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def gen(messages: list, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"steps": steps})]
        )

    return FunctionModel(gen)


def test_generate_goal_returns_steps() -> None:
    from revalid.plan import build_goal_agent, generate_goal

    agent = build_goal_agent(_goal_model(["Re-exercise the reported condition", "Observe the result"]))
    finding = Finding(title="Broken access control", severity=Severity.HIGH)
    steps = generate_goal(agent, finding)
    assert steps == ("Re-exercise the reported condition", "Observe the result")


def test_generate_goal_degrades_to_empty_on_model_failure() -> None:
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from revalid.plan import build_goal_agent, generate_goal

    def boom(messages: list, info: AgentInfo):
        raise RuntimeError("model unavailable")

    agent = build_goal_agent(FunctionModel(boom))
    steps = generate_goal(agent, Finding(title="X", severity=Severity.LOW))
    assert steps == ()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_plan.py -q -k generate_goal`
Expected: FAIL — `ImportError` (no `build_goal_agent`/`generate_goal`).

- [ ] **Step 3: Implement**

In `src/revalid/plan.py`, add near `PlanResult`:

```python
class GeneratedGoal(BaseModel):
    """A short, tool-agnostic retest goal — the steps the agent works to (FR-17 6b-ii)."""

    model_config = ConfigDict(frozen=True)

    steps: tuple[str, ...] = Field(default=(), max_length=6)
```

Add a goal instruction constant (near `_INSTRUCTIONS`):

```python
_GOAL_INSTRUCTIONS = """\
You turn a single pentest finding into a SHORT retest goal: an ordered list of a \
few (2-5) concise, tool-agnostic verification steps that say WHAT to confirm, not \
which tool to use. Make no assumption about the vulnerability class or protocol — \
describe re-exercising the reported condition and observing whether it still \
occurs. Keep each step to one short imperative line.
"""
```

Add the builder + function (mirroring `build_plan_agent`/`generate_plan`, reusing `_finding_prompt`):

```python
def build_goal_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[None, GeneratedGoal]:
    """Build the retest-goal agent (FR-17 6b-ii): a generic, tool-agnostic goal generator."""
    return Agent(
        model if model is not None else resolve_model(),
        output_type=GeneratedGoal,
        instructions=_GOAL_INSTRUCTIONS,
        retries=_MAX_OUTPUT_RETRIES,
        defer_model_check=True,
    )


def generate_goal(agent: Agent[None, GeneratedGoal], finding: Finding) -> tuple[str, ...]:
    """Generate a generic retest goal for ``finding`` (FR-17 6b-ii).

    Best-effort: on any model failure it returns an empty tuple so session start
    never blocks — the agent then falls back to the finding context alone.
    """
    try:
        return agent.run_sync(_finding_prompt(finding)).output.steps
    except UnexpectedModelBehavior:
        return ()
```

(`UnexpectedModelBehavior` is already imported in plan.py; `BaseModel`/`ConfigDict`/`Field` too. Confirm and add any missing to the imports.)

- [ ] **Step 4: Run + gates**

Run: `uv run pytest tests/unit/test_plan.py -q && uv run mypy src/revalid/plan.py && uv run ruff check src/revalid/plan.py tests/unit/test_plan.py`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/plan.py tests/unit/test_plan.py
git commit -m "feat(plan): generic generate_goal — repurpose FR-04 as a tool-agnostic retest goal (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Goal injection in the orchestrator

**Files:** Modify `src/revalid/retest_session.py`; Test `tests/unit/test_retest_session.py` (append).

**Interfaces:**
- Consumes: nothing new.
- Produces: `LiveSession.pending_goal: list[str] | None`, `LiveSession.set_pending_goal(steps)`, `LiveSession.drain_goal()`; `set_goal(session, registry, session_id, steps: list[str]) -> None`. `_resume_with_decision` prepends a drained goal to the next `user_prompt`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_retest_session.py`:

```python
def test_set_goal_records_plan_updated_and_queues_for_agent() -> None:
    """A live goal edit appends a plan_updated event and queues the goal for the agent (6b-ii)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox(
            lambda cmd: CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)
        )
        agent = build_retest_agent(FunctionModel(script_always_propose))
        start_and_step(session, registry, s.id, agent, box, "Retest.")

        rs.set_goal(session, registry, s.id, ["Check the login endpoint", "Confirm the token"])
        live = registry.get(s.id)
        assert live is not None
        assert live.pending_goal == ["Check the login endpoint", "Confirm the token"]
        events = rs.load_events_after(session, s.id, 0)
    updates = [e for e in events if e["kind"] == SessionEventKind.PLAN_UPDATED.value]
    assert updates[-1]["payload"] == {"steps": ["Check the login endpoint", "Confirm the token"]}


def test_set_goal_is_noop_when_not_live() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.set_goal(session, registry, s.id, ["x"])  # not live → no raise, no event
        assert rs.load_events_after(session, s.id, 0) == []


def test_queued_goal_is_injected_into_the_next_turn() -> None:
    """A queued goal reaches the agent as a user turn on the next approval (6b-ii)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        # This scripted model concludes noting whether it saw a user turn mentioning the goal.
        agent = build_retest_agent(FunctionModel(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        cid = _pending_cid(registry, s.id)
        rs.set_goal(session, registry, s.id, ["focus on the admin endpoint"])
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
    # script_run_then_conclude_noting_message sets rationale to "saw-message" when the
    # resume carried a user_prompt — proving the goal injection was delivered.
    assert s.verdict_rationale == "saw-message"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_retest_session.py -q -k "set_goal or queued_goal"`
Expected: FAIL — `AttributeError: module 'revalid.retest_session' has no attribute 'set_goal'` / `pending_goal`.

- [ ] **Step 3: Add `pending_goal` to `LiveSession`**

In the `LiveSession` dataclass (after the `human_messages` block), add:

```python
    #: The current goal (FR-17 6b-ii) queued by an operator edit since the agent's
    #: last turn, delivered as a user turn on the next resume (like human_messages).
    #: ``None`` means no pending change. Guarded by ``lock``.
    pending_goal: list[str] | None = None

    def set_pending_goal(self, steps: list[str]) -> None:
        """Queue a goal change for the agent's next turn (thread-safe)."""
        with self.lock:
            self.pending_goal = list(steps)

    def drain_goal(self) -> list[str] | None:
        """Atomically return and clear the queued goal change (thread-safe)."""
        with self.lock:
            drained = self.pending_goal
            self.pending_goal = None
            return drained
```

- [ ] **Step 4: Add `set_goal`** (near `submit_message`):

```python
def set_goal(
    session: Session, registry: SessionRegistry, session_id: int, steps: list[str]
) -> None:
    """Set the user-owned goal on a live session (FR-17 6b-ii).

    Appends a ``plan_updated`` transcript event (so the "Current goal" panel updates
    and it replays) and queues the goal on the live session; it is delivered to the
    agent as a first-class user turn on the next approve/reject
    (:func:`_resume_with_decision`) — pure-queue, never interrupting a run. A no-op
    if the session is not live (terminal / never started).
    """
    live = registry.get(session_id)
    if live is None:
        return
    append_event(session, session_id, SessionEventKind.PLAN_UPDATED, {"steps": list(steps)})
    live.set_pending_goal(steps)
```

- [ ] **Step 5: Drain the goal in `_resume_with_decision`**

Find `queued = live.drain_messages()` / `user_prompt = "\n".join(queued) if queued else None` and replace with:

```python
    queued = live.drain_messages()
    goal = live.drain_goal()
    user_prompt = _resume_prompt(goal, queued)
```

Add a module-level helper (near `_resume_with_decision`):

```python
def _resume_prompt(goal: list[str] | None, messages: list[str]) -> str | None:
    """Combine a queued goal change + queued operator messages into one user turn."""
    parts: list[str] = []
    if goal:
        steps = "\n".join(f"- {s}" for s in goal)
        parts.append(f"The operator set the goal to:\n{steps}")
    parts.extend(messages)
    return "\n\n".join(parts) if parts else None
```

- [ ] **Step 6: Run + gates**

Run: `uv run pytest tests/unit/test_retest_session.py -q && uv run mypy src/revalid/retest_session.py && uv run ruff check src/revalid/retest_session.py tests/unit/test_retest_session.py && uv run xenon --max-absolute C src/revalid/retest_session.py`
Expected: PASS; mypy + ruff + xenon clean.

- [ ] **Step 7: Commit**

```bash
git add src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "feat(retest): user-owned goal — set_goal + pure-queue injection to the agent (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Seed the goal at session start

**Files:** Modify `src/revalid/app.py`; Test `tests/integration/test_retest_session_api.py` (append).

**Interfaces:**
- Consumes: `generate_goal`/`build_goal_agent` (Task 1); `set_goal`/`PLAN_UPDATED` (Task 2).
- Produces: `get_goal_agent`/`GoalAgentDep`; `run_first_step(..., finding, goal_agent)` (signature change — `prompt` → `finding` + `goal_agent`).

- [ ] **Step 1: Write the failing integration test**

Append to `tests/integration/test_retest_session_api.py` a test + a goal-agent override in `_client()`. First extend `_client()` (and `_echo_client()` if used) to override the goal agent — add after the retest-agent override:

```python
    from revalid.app import get_goal_agent
    from revalid.plan import build_goal_agent
    app.dependency_overrides[get_goal_agent] = lambda: build_goal_agent(
        FunctionModel(_goal_gen(["Re-check the login endpoint", "Confirm the token"]))
    )
```

and add a module-level helper near the top of the test file:

```python
def _goal_gen(steps: list[str]):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def gen(messages: list, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"steps": steps})]
        )

    return FunctionModel(gen)
```

Then the test:

```python
def test_session_start_seeds_a_goal(self=None) -> None:
    """Starting a session generates a goal and shows it as the first plan_updated (6b-ii)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        state = client.get(f"/api/retest-sessions/{sid}").json()
        goal = next(e for e in state["events"] if e["kind"] == "plan_updated")
        assert goal["payload"]["steps"] == ["Re-check the login endpoint", "Confirm the token"]
```

(Drop the `self=None` — plain function. Shown here only to avoid a lint reflow; write it as `def test_session_start_seeds_a_goal() -> None:`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_retest_session_api.py::test_session_start_seeds_a_goal -q`
Expected: FAIL — no `plan_updated` event (nothing seeds a goal yet) / `get_goal_agent` import error.

- [ ] **Step 3: Add the goal-agent DI**

In `src/revalid/app.py`, near `get_plan_agent`:

```python
def get_goal_agent(settings: SettingsDep) -> Agent[None, GeneratedGoal]:
    """Yield the FR-17 goal agent built from the persisted setting (ADR-0021)."""
    return build_goal_agent(build_model(settings))
```

and near `PlanAgentDep`:

```python
GoalAgentDep = Annotated[Agent[None, GeneratedGoal], Depends(get_goal_agent)]
```

Extend the plan import: `from revalid.plan import (..., GeneratedGoal, build_goal_agent, generate_goal, ...)` (keep the existing names). Verify the import survives the write hook (Tasks below use these names).

- [ ] **Step 4: Seed in `run_first_step`**

Change `run_first_step`'s signature: replace the `prompt: str` parameter with `finding: Finding` and `goal_agent: Agent[None, GeneratedGoal]`. In the body, inside the `try` (after computing `record`/budget, before `start_and_step`), build the seeded prompt and emit the initial goal:

```python
            goal: tuple[str, ...] = ()
            with contextlib.suppress(Exception):  # goal gen is best-effort; never blocks start
                goal = generate_goal(goal_agent, finding)
            if goal:
                append_event(
                    session, session_id, SessionEventKind.PLAN_UPDATED, {"steps": list(goal)}
                )
            start_and_step(
                session,
                registry,
                session_id,
                agent,
                sandbox,
                _goal_prompt(goal, finding),
                max_steps=max_steps,
                free_launch=free_launch,
                max_seconds=max_seconds,
            )
```

Add `append_event` + `SessionEventKind` to the imports if not present (both are — `SessionEventKind` via `revalid.domain`, `append_event` via `revalid.retest_session`; confirm and add). Add a `_goal_prompt` helper next to `_finding_prompt`:

```python
def _goal_prompt(goal: tuple[str, ...], finding: Finding) -> str:
    """Prepend the current goal (if any) to the finding context for the agent (6b-ii)."""
    base = _finding_prompt(finding)
    if not goal:
        return base
    steps = "\n".join(f"- {s}" for s in goal)
    return f"Current goal:\n{steps}\n\n{base}"
```

- [ ] **Step 5: Wire `start_retest_session`**

Add `goal_agent: GoalAgentDep` to the route's params, and change the `background.add_task(...)` call to pass the finding + goal agent instead of the pre-built prompt:

```python
        finding = version.to_domain()
        record = create_session(...)  # unchanged
        background.add_task(
            run_first_step, sessions, registry, record.id, agent, make_sandbox, finding, goal_agent
        )
```

Delete the now-unused `prompt = _finding_prompt(version.to_domain())` line (the prompt is built inside `run_first_step` now via `_goal_prompt`).

- [ ] **Step 6: Run + gates**

Run: `uv run pytest tests/integration/test_retest_session_api.py tests/unit -q && uv run mypy && uv run ruff check src/revalid/app.py tests/integration/test_retest_session_api.py && uv run xenon --max-absolute C src/revalid/app.py`
Expected: PASS; mypy (bare) clean; ruff + xenon clean. (Existing session tests still pass — the goal is additive; a stand-in goal agent returns the scripted steps.)

- [ ] **Step 7: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(api): seed a generated goal at session start (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Goal edit + regenerate endpoints

**Files:** Modify `src/revalid/app.py`; Test `tests/integration/test_retest_session_api.py` (append).

**Interfaces:**
- Consumes: `set_goal` (Task 2); `generate_goal`/`GoalAgentDep` (Tasks 1/3).
- Produces: `POST /api/retest-sessions/{id}/goal {steps}` and `POST /api/retest-sessions/{id}/goal/regenerate`.

- [ ] **Step 1: Write the failing integration tests**

```python
def test_set_goal_endpoint_updates_the_panel_event() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        resp = client.post(
            f"/api/retest-sessions/{sid}/goal", json={"steps": ["Only test /admin"]}
        )
        assert resp.status_code == 202
        state = client.get(f"/api/retest-sessions/{sid}").json()
        updates = [e for e in state["events"] if e["kind"] == "plan_updated"]
        assert updates[-1]["payload"]["steps"] == ["Only test /admin"]


def test_regenerate_goal_endpoint_reseeds() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        resp = client.post(f"/api/retest-sessions/{sid}/goal/regenerate")
        assert resp.status_code == 202
        state = client.get(f"/api/retest-sessions/{sid}").json()
        # The stand-in goal agent re-emits its scripted steps as a fresh plan_updated.
        updates = [e for e in state["events"] if e["kind"] == "plan_updated"]
        assert updates[-1]["payload"]["steps"] == ["Re-check the login endpoint", "Confirm the token"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_retest_session_api.py -q -k "set_goal_endpoint or regenerate_goal"`
Expected: FAIL — 404/405 (routes not registered).

- [ ] **Step 3: Add the request model + workers**

In `src/revalid/app.py`, near the other request models:

```python
class GoalRequest(BaseModel):
    """Body for a user-owned goal edit (FR-17 6b-ii)."""

    steps: list[str]
```

Near `run_message`:

```python
def run_goal(
    sessions: sessionmaker[Session], registry: SessionRegistry, session_id: int, steps: list[str]
) -> None:
    """Set the user-owned goal on a session (FR-17 6b-ii background task)."""
    with sessions() as session:
        set_goal(session, registry, session_id, steps)


def run_regenerate_goal(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    goal_agent: Agent[None, GeneratedGoal],
    finding: Finding,
) -> None:
    """Regenerate + set the goal for a session (FR-17 6b-ii background task)."""
    with sessions() as session:
        set_goal(session, registry, session_id, list(generate_goal(goal_agent, finding)))
```

Add `set_goal` to the `from revalid.retest_session import (...)` block (verify it survives the write hook).

- [ ] **Step 4: Register the routes**

Add a `_register_goal_routes` (mirroring `_register_adjudicate_route`; keeps each registrar under the mccabe gate), and call it in `create_app` next to `_register_adjudicate_route`:

```python
def _register_goal_routes(
    router: APIRouter, sessions: sessionmaker[Session], registry: SessionRegistry
) -> None:
    """Register the FR-17 6b-ii user-owned goal routes (edit + regenerate)."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/retest-sessions/{session_id}/goal", status_code=202)
    def set_goal_route(
        session_id: int, body: GoalRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Set the user-owned goal on a live session; delivered to the agent next turn (6b-ii)."""
        background.add_task(run_goal, sessions, registry, session_id, body.steps)
        return {"status": "accepted"}

    @router.post("/retest-sessions/{session_id}/goal/regenerate", status_code=202)
    def regenerate_goal_route(
        session_id: int,
        session: SessionDep,
        goal_agent: GoalAgentDep,
        background: BackgroundTasks,
    ) -> dict[str, str]:
        """Regenerate the goal for a session's finding and set it (6b-ii)."""
        record = session.get(RetestSessionRecord, session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown session")
        finding = _current_or_404(session, record.finding_id).to_domain()
        background.add_task(
            run_regenerate_goal, sessions, registry, session_id, goal_agent, finding
        )
        return {"status": "accepted"}
```

In `create_app`, add `_register_goal_routes(api, sessions, registry)` after `_register_adjudicate_route(...)`.

- [ ] **Step 5: Run + gates + live smoke**

Run: `uv run pytest tests/integration/test_retest_session_api.py -q && uv run mypy && uv run ruff check src/revalid/app.py && uv run xenon --max-absolute C src/revalid/app.py`
Expected: PASS; all clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(api): POST /goal + /goal/regenerate for the user-owned goal (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Frontend — "Current goal" panel with Edit + Regenerate

**Files:** Modify `frontend/src/api/client.ts`, `frontend/src/routes/RetestSession.tsx` (+ `.test.tsx`).

- [ ] **Step 1: Add the client functions**

In `frontend/src/api/client.ts`, near `submitMessage`:

```typescript
/** Set the user-owned goal for a session (FR-17 6b-ii); delivered to the agent next turn. */
export function setSessionGoal(id: number, steps: string[]): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/goal`,
    jsonInit("POST", { steps }),
  );
}

/** Regenerate the goal for a session's finding (FR-17 6b-ii). */
export function regenerateSessionGoal(id: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/retest-sessions/${String(id)}/goal/regenerate`, {
    method: "POST",
  });
}
```

- [ ] **Step 2: Write the failing test**

In `RetestSession.test.tsx`, add a test: a live session shows the goal panel labelled "Current goal" with an Edit control that calls `setSessionGoal`, and a Regenerate button that calls `regenerateSessionGoal`:

```typescript
it("edits the current goal (FR-17 6b-ii)", async () => {
  vi.mocked(hook.useRetestSession).mockReturnValue({
    events: [{ seq: 1, kind: "plan_updated", payload: { steps: ["Old step"] } }],
    status: "awaiting_command",
    verdict: null,
    connected: true,
  });
  vi.mocked(client.setSessionGoal).mockResolvedValue({ status: "accepted" });

  renderAt(1);
  await userEvent.click(screen.getByRole("button", { name: /edit goal/i }));
  const box = screen.getByLabelText(/goal steps/i);
  await userEvent.clear(box);
  await userEvent.type(box, "Check /admin\nConfirm 200");
  await userEvent.click(screen.getByRole("button", { name: /save goal/i }));
  expect(client.setSessionGoal).toHaveBeenCalledWith(1, ["Check /admin", "Confirm 200"]);
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npx vitest run src/routes/RetestSession.test.tsx`
Expected: FAIL — no "Edit goal" control.

- [ ] **Step 4: Implement the panel controls**

In `frontend/src/routes/RetestSession.tsx`: import `setSessionGoal, regenerateSessionGoal`; add two mutations (edit + regenerate) and an `editingGoal` state + a textarea (one step per line). Relabel the panel `<Eyebrow>Plan</Eyebrow>` to `<Eyebrow>Current goal</Eyebrow>`. When not editing, show the `StepList` (or the "No goal set yet" placeholder) + an **Edit goal** button (live sessions only, `!isOver(status)`) + a **Regenerate** button. When editing, show a `<textarea aria-label="goal steps">` seeded from `planSteps.join("\n")` + **Save goal** (calls `setSessionGoal(id, value.split("\n").map(s=>s.trim()).filter(Boolean))`) and **Cancel**. Keep edits disabled once the session is over.

- [ ] **Step 5: Run the frontend gates**

Run: `cd frontend && npx vitest run --coverage && npx tsc --noEmit && npx eslint src && npm run build`
Expected: all green; coverage floor met.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/RetestSession.tsx frontend/src/routes/RetestSession.test.tsx
git commit -m "feat(ui): Current goal panel with Edit + Regenerate (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: SRS + roadmap + full verification + PR

- [ ] **Step 1:** Add SRS FR-17 acceptance criteria for 6b-ii (AC18+): a generic goal is generated + shown + given to the agent at start (degrades to empty on failure); the user edits/regenerates it live and it reaches the agent on the next turn (pure-queue); the agent no longer proposes the plan (`set_plan` removed, 6b-ii-a). Mark the 6b-ii block done.

- [ ] **Step 2:** Add a roadmap entry: 6b-ii-b built — the user-owned goal completes 6b-ii; note 6b-iii (retire batch execution + finding-flow reshape) remains.

- [ ] **Step 3: Full gate**

Run: `uv run pytest tests/unit tests/integration -q && uv run pytest --cov=src/revalid -q && uv run mypy && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run xenon --max-absolute C src`
Expected: all green; coverage ≥ 80%.

Run: `cd frontend && npx tsc --noEmit && npx eslint src && npx vitest run --coverage && npm run build`
Expected: all green.

Run: `make demo-retest-session` — still green (goal seeding is on the API path, not the demo's direct orchestrator calls).

- [ ] **Step 4: Push + PR**

```bash
git add docs/
git commit -m "docs(retest): SRS + roadmap for the user-owned goal (FR-17 6b-ii-b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git push
```

Open the PR titled "FR-17 Slice 6b-ii-b: the user-owned goal (FR-04 repurposed)" with a filled "How to validate". Body MUST contain **`Closes #107`** (exactly once, as the close directive — do NOT write that phrase anywhere else). Queue squash auto-merge; monitor CI to green.

---

## Self-Review (completed during authoring)

- **Spec coverage:** §2.1 generate_goal → Task 1; §2.3 seed at start → Task 3; §2.4 edit/regenerate + injection → Tasks 2/4; §2.6 frontend → Task 5; §4 AC → Task 6. §2.2 (reuse panel) is honored throughout (the panel reads `plan_updated`, now written by `set_goal`). §2.5 (`set_plan` removal) already shipped in 6b-ii-a.
- **Placeholder scan:** none — every code step shows the code.
- **Type consistency:** `generate_goal(agent, finding) -> tuple[str,...]` used identically in Tasks 1/3/4; `set_goal(session, registry, session_id, steps: list[str])` in Tasks 2/4; `GeneratedGoal`/`GoalAgentDep` names consistent; `run_first_step(..., finding, goal_agent)` new signature used only by `start_retest_session` (Task 3).
