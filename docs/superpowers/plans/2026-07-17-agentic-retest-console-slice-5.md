# FR-17 Slice 5 — Free-Launch + Budget/Give-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a free-launch mode (auto-approve the agent's commands; plan changes stay gated), a configurable + visible step budget, a free-launch-only wall-clock budget, and a distinct given-up state to the FR-17 agentic retest console.

**Architecture:** Free-launch reuses the existing gate rather than forking it — a new iterative `_drive_auto` loop auto-approves successive command proposals by calling `_resume_with_decision` directly (one agent turn per iteration, no recursion), stopping when the agent concludes, proposes a `set_plan` (always gated), or a budget bound trips. Config (`free_launch`, `max_steps`, `max_seconds`) is persisted on `retest_sessions`, settable at session start and via a live toggle endpoint; the SPA renders a budget meter, a toggle, auto-tagged commands, and a given-up banner.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (SQLite, `create_all` — no Alembic), Pydantic AI (TestModel/FunctionModel in tests), pytest; React/TS/Vite/Tailwind, TanStack Query, vitest.

## Global Constraints

- Python 3.12+, managed with `uv`; run tools via `uv run` / `make`.
- `mypy --strict` must pass; ruff lint + format (line length 100, Google docstrings on public API).
- Complexity gate: xenon max absolute **C**; refactor, never suppress.
- Coverage ≥ 80% on `src/`; new pure/logic lines aim for 100% (live Docker/PTY lines excluded, matching the `sandbox`/`browser` precedent).
- Tests per pyramid level: `tests/unit/` (no I/O, LLM via TestModel/FunctionModel), `tests/integration/` (marker `integration`, real REST/WS + `FakeSandbox`).
- Conventional Commits; every commit carries `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Frontend gates: eslint + `tsc` + `vite build` + vitest all green; `RetestSession`-owned pure logic stays pinned per the two-tier coverage floor.
- The sandbox egress lock (NFR-03) and single-user threat model (ADR-0008) are **untouched** — free-launch removes the human pause, not the containment.
- Branch: `feat/fr17-free-launch-slice5`. PR body must contain `Closes #100`.

---

## File Structure

**Backend:**
- `src/revalid/domain.py` — add `SessionEventKind.FREE_LAUNCH_CHANGED`.
- `src/revalid/db.py` — add `free_launch` / `max_steps` / `max_seconds` columns to `RetestSessionRecord`.
- `src/revalid/retest_session.py` — extend `LiveSession`, `create_session`, `start_and_step`; add `_drive_auto`, `set_free_launch`, a time-budget helper; refactor `apply_decision`'s tail so the auto-drive hooks in without recursion.
- `src/revalid/app.py` — `StartSessionRequest` + `FreeLaunchRequest` models; optional body on `start_retest_session`; new `/free-launch` route + `run_free_launch` worker; `run_first_step` seeds config; `RetestSessionOut` carries the three fields.

**Frontend:**
- `frontend/src/api/client.ts` — extend `RetestSession`; add `setFreeLaunch`; optional opts on `startRetestSession`.
- `frontend/src/lib/sessionBudget.ts` (new) — pure helpers: `stepsUsed`, `currentFreeLaunch`, `budgetLabel`, `givenUpReason`.
- `frontend/src/lib/sessionBudget.test.ts` (new) — vitest for the pure helpers.
- `frontend/src/routes/RetestSession.tsx` — budget meter, free-launch toggle, auto-tag, given-up banner.
- `frontend/src/hooks/useRetestSession.ts` — unchanged (WS events); the component fetches the record via TanStack Query for config fields.

**Docs:**
- `docs/adr/0029-agentic-retest-free-launch.md` (new).
- `docs/requirements/srs.md` — FR-17 AC9–AC12.
- `docs/roadmap.md` — M6 Slice 5 note + checkbox.

---

## Task 1: Data model + config plumbing (no behaviour change yet)

**Files:**
- Modify: `src/revalid/domain.py` (`SessionEventKind`)
- Modify: `src/revalid/db.py` (`RetestSessionRecord`)
- Modify: `src/revalid/retest_session.py` (`create_session`, `LiveSession`)
- Modify: `src/revalid/app.py` (`RetestSessionOut`)
- Test: `tests/unit/test_retest_session.py`, `tests/unit/test_db.py` (if present; else fold into test_retest_session)

**Interfaces:**
- Produces: `SessionEventKind.FREE_LAUNCH_CHANGED = "free_launch_changed"`; `RetestSessionRecord.free_launch: bool`, `.max_steps: int`, `.max_seconds: int | None`; `create_session(session, *, finding_id, model, free_launch=False, max_steps=8, max_seconds=None)`; `LiveSession(..., free_launch=False, max_seconds=None, started_at=<monotonic>)`; `RetestSessionOut` fields `free_launch`, `max_steps`, `max_seconds`.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_retest_session.py`)

```python
def test_create_session_persists_budget_config(session_factory):
    with session_factory() as session:
        finding_id = _seed_finding(session)
        record = create_session(
            session,
            finding_id=finding_id,
            model="test",
            free_launch=True,
            max_steps=20,
            max_seconds=300,
        )
        assert record.free_launch is True
        assert record.max_steps == 20
        assert record.max_seconds == 300


def test_create_session_budget_config_defaults(session_factory):
    with session_factory() as session:
        finding_id = _seed_finding(session)
        record = create_session(session, finding_id=finding_id, model="test")
        assert record.free_launch is False
        assert record.max_steps == 8
        assert record.max_seconds is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_retest_session.py::test_create_session_persists_budget_config -v`
Expected: FAIL — `create_session() got an unexpected keyword argument 'free_launch'`.

- [ ] **Step 3: Add the enum member** (`src/revalid/domain.py`, in `SessionEventKind`, after `STATE_CHANGE`)

```python
    STATE_CHANGE = "state_change"
    FREE_LAUNCH_CHANGED = "free_launch_changed"
```

- [ ] **Step 4: Add the columns** (`src/revalid/db.py`, in `RetestSessionRecord`, after `ended_at`)

```python
    free_launch: Mapped[bool] = mapped_column(default=False)
    max_steps: Mapped[int] = mapped_column(default=8)
    max_seconds: Mapped[int | None] = mapped_column(default=None)
```

- [ ] **Step 5: Extend `create_session`** (`src/revalid/retest_session.py`)

```python
def create_session(
    session: Session,
    *,
    finding_id: int,
    model: str,
    free_launch: bool = False,
    max_steps: int = 8,
    max_seconds: int | None = None,
) -> RetestSessionRecord:
    """Insert a ``starting`` session row and return it.

    Args:
        session: Active DB session.
        finding_id: The finding identity (FR-16) this session retests.
        model: The resolved LLM model string driving the agent.
        free_launch: Whether the agent's commands auto-run without a per-command
            human approval (plan changes stay gated regardless). FR-17 Slice 5.
        max_steps: Step budget — commands approved before force-conclude.
        max_seconds: Wall-clock budget in seconds, enforced only in free-launch
            mode; ``None`` means no time bound.
    """
    record = RetestSessionRecord(
        finding_id=finding_id,
        status=RetestSessionStatus.STARTING.value,
        model=model,
        free_launch=free_launch,
        max_steps=max_steps,
        max_seconds=max_seconds,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
```

- [ ] **Step 6: Extend `LiveSession`** (`src/revalid/retest_session.py`) — add fields and a start stamp. Add `import time` at the top if absent.

```python
    free_launch: bool = False
    #: Wall-clock budget in seconds (free-launch only); ``None`` = no time bound.
    max_seconds: float | None = None
    #: The wall-clock budget's origin, stamped by ``start_and_step`` from its
    #: injected ``clock`` (``time.monotonic`` in production). The default is a
    #: safe fallback for a directly-constructed live session.
    started_at: float = field(default_factory=time.monotonic)
```

- [ ] **Step 7: Extend `RetestSessionOut`** (`src/revalid/app.py`) — add the three fields and map them in `from_record`.

```python
    verdict_rationale: str | None
    free_launch: bool
    max_steps: int
    max_seconds: int | None
    events: list[SessionEventOut] = []
```

and inside `from_record`, after `verdict_rationale=record.verdict_rationale,`:

```python
            free_launch=record.free_launch,
            max_steps=record.max_steps,
            max_seconds=record.max_seconds,
```

- [ ] **Step 8: Run the new tests + the retest suite**

Run: `uv run pytest tests/unit/test_retest_session.py -q`
Expected: PASS (including the two new tests). Existing `start_and_step(..., max_steps=8)` calls are unaffected — that param is untouched here.

- [ ] **Step 9: Typecheck + lint**

Run: `uv run mypy --strict src/revalid/retest_session.py src/revalid/db.py src/revalid/app.py src/revalid/domain.py && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/revalid/domain.py src/revalid/db.py src/revalid/retest_session.py src/revalid/app.py tests/unit/test_retest_session.py
git commit -m "feat(retest): persist free-launch + budget config on sessions (FR-17 Slice 5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: The free-launch drive loop + budgets (orchestrator core)

This is the behavioural heart. `_drive_auto` auto-approves successive **command** proposals by calling `_resume_with_decision` directly — iterative, one agent turn per loop pass, never recursive. Plan proposals stop the loop (always gated). Both budgets exit through the shared give-up path.

**Files:**
- Modify: `src/revalid/retest_session.py` (`start_and_step`, `apply_decision`, add `_drive_auto`, `_time_budget_exhausted`, refactor decision tail)
- Test: `tests/unit/test_retest_session.py`

**Interfaces:**
- Consumes: `_consume_pending_call`, `_resume_with_decision`, `_step_budget_exhausted`, `record_verdict`, `_mark_given_up`, `_teardown`, `append_event`, `SessionEventKind.COMMAND_APPROVED`, `LiveSession.{free_launch,max_seconds,started_at,pending_kind,pending_call_id}` (Task 1).
- Produces: `start_and_step(session, registry, session_id, agent, sandbox, finding_prompt, *, max_steps=8, free_launch=False, max_seconds=None)`; `_drive_auto(session, registry, session_id, *, clock=time.monotonic) -> None`; auto-approvals recorded as `COMMAND_APPROVED` with payload `{"auto": True}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_retest_session.py`). These use the existing `FunctionModel`/`FakeSandbox` helpers in that file. `_free_launch_agent` scripts two commands then a verdict; assert no human decision was needed and both approvals are auto-flagged.

```python
def _two_commands_then_conclude(messages, info):
    """FunctionModel: propose run_command twice, then conclude still_open."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    calls = sum(
        1
        for m in messages
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolCallPart) and p.tool_name == "run_command"
    )
    if calls == 0:
        return ModelResponse(parts=[ToolCallPart("run_command", {"command": "id", "rationale": "who"})])
    if calls == 1:
        return ModelResponse(parts=[ToolCallPart("run_command", {"command": "whoami", "rationale": "again"})])
    return ModelResponse(parts=[ToolCallPart("conclude", {"status": "still_open", "rationale": "done"})])


def test_free_launch_auto_runs_commands_to_verdict(session_factory):
    with session_factory() as session:
        # start_and_step drives the free-launch loop before _start_live returns.
        session_id, registry, sandbox = _start_live(
            session, _two_commands_then_conclude, free_launch=True
        )
        record = session.get(RetestSessionRecord, session_id)
        assert record.status == RetestSessionStatus.CONCLUDED.value
        assert record.verdict_status == VerdictStatus.STILL_OPEN.value
        approvals = [
            e for e in load_events_after(session, session_id, 0)
            if e["kind"] == SessionEventKind.COMMAND_APPROVED.value
        ]
        assert len(approvals) == 2
        assert all(e["payload"].get("auto") is True for e in approvals)
        # No human command_rejected / manual approval events were needed.


def test_free_launch_still_gates_plan_changes(session_factory):
    with session_factory() as session:
        session_id, registry, sandbox = _start_live(
            session, _propose_plan_then_command, free_launch=True
        )
        record = session.get(RetestSessionRecord, session_id)
        # A set_plan proposal halts the auto-loop: the session waits for approval.
        assert record.status == RetestSessionStatus.AWAITING_PLAN.value


def test_free_launch_step_budget_gives_up(session_factory):
    with session_factory() as session:
        session_id, registry, sandbox = _start_live(
            session, _always_propose_command, free_launch=True, max_steps=2
        )
        record = session.get(RetestSessionRecord, session_id)
        assert record.status == RetestSessionStatus.GIVEN_UP.value
        assert record.verdict_rationale == "budget exhausted"


def test_free_launch_time_budget_gives_up(session_factory):
    with session_factory() as session:
        # One injected clock feeds BOTH the started_at baseline (first call,
        # inside start_and_step) and the drive loop's budget check (later calls):
        # 0.0 then 10_000.0 → elapsed 10_000 > max_seconds 1 on the first
        # boundary, before any command runs. Deterministic; never real time.
        clock = iter([0.0, 10_000.0, 10_000.0, 10_000.0]).__next__
        session_id, registry, sandbox = _start_live(
            session, _always_propose_command, free_launch=True, max_seconds=1, clock=clock
        )
        record = session.get(RetestSessionRecord, session_id)
        assert record.status == RetestSessionStatus.GIVEN_UP.value
        assert record.verdict_rationale == "time budget exhausted"
```

Add the small helpers near the other test helpers (reuse patterns already in the file — `_seed_finding`, the `FunctionModel`/agent build used by existing tests). `start_and_step` **drives the free-launch loop itself** (Step 5 below), so `_start_live` returns *after* the loop has run to a verdict / gate / budget — the tests assert on the final state directly, they never call `_drive_auto`. If a `_start_live` helper does not already exist, add it:

```python
def _start_live(session, model_fn, *, free_launch=False, max_steps=8, max_seconds=None, clock=None):
    """Seed a finding, build a FunctionModel agent + FakeSandbox, run the first step.

    In free-launch, start_and_step's own _drive_auto loop runs before this
    returns, so callers assert on the resulting terminal/awaiting state.
    """
    finding_id = _seed_finding(session)
    record = create_session(
        session, finding_id=finding_id, model="test",
        free_launch=free_launch, max_steps=max_steps, max_seconds=max_seconds,
    )
    registry = SessionRegistry()
    sandbox = FakeSandbox(...)   # match the existing FakeSandbox construction in this file
    agent = _build_agent(FunctionModel(model_fn))  # match existing agent-build helper
    kwargs = {} if clock is None else {"clock": clock}
    start_and_step(
        session, registry, record.id, agent, sandbox, "goal",
        max_steps=max_steps, free_launch=free_launch, max_seconds=max_seconds, **kwargs,
    )
    return record.id, registry, sandbox
```

> Note for the implementer: `_always_propose_command`, `_propose_plan_then_command`, `FakeSandbox`, and the agent-build helper follow the exact patterns already in `tests/unit/test_retest_session.py` (see `test_budget_exhaustion_gives_up`, `test_plan_approval_is_exempt_from_command_budget`). Reuse them.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_retest_session.py -k free_launch -v`
Expected: FAIL — `_drive_auto` undefined and `start_and_step` rejects `free_launch`/`max_seconds`.

- [ ] **Step 3: Seed config into `start_and_step`** (`src/revalid/retest_session.py`). The single `clock` param feeds both the `started_at` baseline and the drive loop's budget check, so a test that injects one clock controls the whole wall-clock calculation.

```python
def start_and_step(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests],
    sandbox: Sandbox,
    finding_prompt: str,
    *,
    max_steps: int = 8,
    free_launch: bool = False,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
```

and build the live session with the new fields, stamping `started_at` from `clock`:

```python
    sandbox.start()
    live = LiveSession(
        agent=agent,
        sandbox=sandbox,
        max_steps=max_steps,
        free_launch=free_launch,
        max_seconds=max_seconds,
        started_at=clock(),
    )
```

The existing tail (`try/except` around `agent.run_sync` + `_dispatch_output`) is unchanged here; the drive-loop hook is added in Step 5. Keep the `clock` in scope for that hook.

- [ ] **Step 4: Add the time-budget helper + the drive loop** (`src/revalid/retest_session.py`, near `_step_budget_exhausted`). Ensure `import time` and `from typing import Callable` are present.

```python
def _time_budget_exhausted(live: LiveSession, clock: Callable[[], float]) -> bool:
    """True when a free-launch session has exceeded its wall-clock budget.

    Checked only at step boundaries (the orchestrator holds control between
    agent turns, never mid-turn) and only in free-launch mode — in gated mode
    the elapsed time would include human think-time and trip falsely.
    """
    if live.max_seconds is None:
        return False
    return clock() - live.started_at > live.max_seconds


def _drive_auto(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Auto-approve successive command proposals while free-launch is on.

    The free-launch loop (FR-17 Slice 5). Iterative on purpose — one
    :func:`_resume_with_decision` call per pass, never recursing through
    :func:`apply_decision` — so a large ``max_steps`` cannot blow the stack.
    Each auto-approval goes through the same compare-and-swap
    (:func:`_consume_pending_call`) and step-budget check as a human approval,
    and is recorded as a ``command_approved`` event flagged ``{"auto": True}``
    so the transcript stays honest about what a human vetted.

    The loop stops when: the session is torn down (concluded / gave up), the
    agent proposes a ``set_plan`` (``pending_kind != "command"`` — plan changes
    are **always** gated), free-launch is turned off, or a budget bound trips.
    """
    while True:
        live = registry.get(session_id)
        if (
            live is None
            or not live.free_launch
            or live.pending_kind != "command"
            or live.pending_call_id is None
        ):
            return
        if _time_budget_exhausted(live, clock):
            record_verdict(session, session_id, VerdictStatus.INCONCLUSIVE, "time budget exhausted")
            _mark_given_up(session, session_id)
            _teardown(registry, session_id)
            return
        call_id = _consume_pending_call(live, live.pending_call_id)
        if call_id is None:
            return  # a concurrent human decision took the pending command
        append_event(session, session_id, SessionEventKind.COMMAND_APPROVED, {"auto": True})
        _resume_with_decision(
            session, registry, session_id, live, call_id, approved=True, reason=""
        )
```

- [ ] **Step 5: Hook the loop into the two boundaries** — `start_and_step` (after the first `_dispatch_output`, forwarding its `clock`) and `apply_decision` (after `_resume_with_decision`, default clock — a human-triggered continuation uses real time against the real `started_at`).

In `start_and_step`, replace the trailing `_dispatch_output(...)` call with:

```python
    _dispatch_output(session, registry, session_id, result)
    _drive_auto(session, registry, session_id, clock=clock)
```

In `apply_decision`, after the existing `_resume_with_decision(...)` call at the end, add `_drive_auto`:

```python
    _resume_with_decision(
        session, registry, session_id, live, call_id, approved=approved, reason=reason
    )
    _drive_auto(session, registry, session_id)
```

> Why this is not recursive: `_drive_auto` calls **`_resume_with_decision`** (not `apply_decision`), and neither `_resume_with_decision` nor `_dispatch_output` calls `_drive_auto`. So the only callers of `_drive_auto` are `start_and_step` and `apply_decision`, and `_drive_auto` never calls either. Depth stays 1 regardless of step count. (In gated mode both hooks are inert: `_drive_auto`'s first guard `not live.free_launch` returns immediately, so the existing gated path is byte-for-byte unchanged.)

- [ ] **Step 6: Run the free-launch tests**

Run: `uv run pytest tests/unit/test_retest_session.py -k free_launch -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the whole retest suite (guard against regressions in the gated path)**

Run: `uv run pytest tests/unit/test_retest_session.py -q`
Expected: PASS — the gated path is unchanged (in gated mode `_drive_auto` returns immediately on the `not live.free_launch` guard, so `apply_decision`/`start_and_step` behave exactly as before).

- [ ] **Step 8: Complexity + typecheck**

Run: `uv run xenon --max-absolute C src/revalid/retest_session.py && uv run mypy --strict src/revalid/retest_session.py`
Expected: no errors. If `_drive_auto` trips xenon, extract the give-up arm into a helper (`_give_up(session, registry, session_id, reason)` wrapping `record_verdict`+`_mark_given_up`+`_teardown`) and reuse it for the step budget too.

- [ ] **Step 9: Commit**

```bash
git add src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "feat(retest): free-launch auto-approve loop + wall-clock budget (FR-17 Slice 5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Live toggle (`set_free_launch`) + mid-session auto-approve

**Files:**
- Modify: `src/revalid/retest_session.py` (add `set_free_launch`)
- Test: `tests/unit/test_retest_session.py`

**Interfaces:**
- Consumes: `_drive_auto` (Task 2), `append_event`, `SessionEventKind.FREE_LAUNCH_CHANGED`, `RetestSessionRecord`.
- Produces: `set_free_launch(session, registry, session_id, enabled: bool, *, clock=time.monotonic) -> None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_retest_session.py`)

```python
def test_enable_free_launch_auto_approves_pending_command(session_factory):
    with session_factory() as session:
        # Start gated: the first command proposal sits awaiting a human decision.
        session_id, registry, sandbox = _start_live(
            session, _one_command_then_conclude, free_launch=False
        )
        assert session.get(RetestSessionRecord, session_id).status == (
            RetestSessionStatus.AWAITING_COMMAND.value
        )
        set_free_launch(session, registry, session_id, True)
        record = session.get(RetestSessionRecord, session_id)
        # Enabling drained the pending command and drove it to a verdict.
        assert record.status == RetestSessionStatus.CONCLUDED.value
        assert record.free_launch is True
        kinds = [e["kind"] for e in load_events_after(session, session_id, 0)]
        assert SessionEventKind.FREE_LAUNCH_CHANGED.value in kinds


def test_disable_free_launch_records_event(session_factory):
    with session_factory() as session:
        session_id, registry, sandbox = _start_live(
            session, _one_command_then_conclude, free_launch=True
        )
        set_free_launch(session, registry, session_id, False)
        live = registry.get(session_id)
        if live is not None:  # still live only if not yet concluded
            assert live.free_launch is False
        events = load_events_after(session, session_id, 0)
        toggles = [e for e in events if e["kind"] == SessionEventKind.FREE_LAUNCH_CHANGED.value]
        assert toggles[-1]["payload"] == {"enabled": False}


def test_set_free_launch_noop_when_not_live(session_factory):
    with session_factory() as session:
        finding_id = _seed_finding(session)
        record = create_session(session, finding_id=finding_id, model="test")
        registry = SessionRegistry()  # nothing registered
        set_free_launch(session, registry, record.id, True)  # no raise
        assert record.free_launch is False  # unchanged: no live session to toggle
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_retest_session.py -k free_launch_ -v`
Expected: FAIL — `set_free_launch` undefined.

- [ ] **Step 3: Implement `set_free_launch`** (`src/revalid/retest_session.py`)

```python
def set_free_launch(
    session: Session,
    registry: SessionRegistry,
    session_id: int,
    enabled: bool,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Toggle free-launch on a live session (FR-17 Slice 5).

    Updates the persisted mode + the live flag, records a
    ``free_launch_changed`` transcript event, and — when enabling with a
    command already pending — auto-approves it (and any that follow) via
    :func:`_drive_auto`. A no-op if the session is not live (already
    ended/concluded, or never started): there is nothing to steer once torn
    down, and the persisted mode is fixed at that point.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry.
        session_id: The retest session to toggle.
        enabled: The new free-launch state.
        clock: Monotonic clock for the wall-clock budget check (injectable
            for tests); forwarded to :func:`_drive_auto`.
    """
    live = registry.get(session_id)
    if live is None:
        return
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    record.free_launch = enabled
    session.commit()
    live.free_launch = enabled
    append_event(session, session_id, SessionEventKind.FREE_LAUNCH_CHANGED, {"enabled": enabled})
    if enabled:
        _drive_auto(session, registry, session_id, clock=clock)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_retest_session.py -k free_launch -v`
Expected: PASS.

- [ ] **Step 5: Typecheck + lint**

Run: `uv run mypy --strict src/revalid/retest_session.py && uv run ruff check src tests`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "feat(retest): set_free_launch live toggle, auto-approves pending command (FR-17 Slice 5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: REST surface — session-start body + `/free-launch` endpoint

**Files:**
- Modify: `src/revalid/app.py` (`StartSessionRequest`, `FreeLaunchRequest`, `start_retest_session`, `run_first_step`, `run_free_launch`, route registration)
- Test: `tests/integration/test_retest_session_api.py`

**Interfaces:**
- Consumes: `create_session` (Task 1), `start_and_step` config params (Task 2), `set_free_launch` (Task 3), `RetestSessionOut` fields (Task 1).
- Produces: `POST /findings/{id}/retest-session` optional body `{free_launch?, max_steps?, max_seconds?}`; `POST /retest-sessions/{id}/free-launch` body `{enabled: bool}` → `202`; `run_free_launch(sessions, registry, session_id, enabled)` background worker.

- [ ] **Step 1: Write the failing integration tests** (append to `tests/integration/test_retest_session_api.py`; reuse `_echo_client` / `_client` fixtures already in that file for a FunctionModel-backed app + `FakeSandbox`). Match the existing fixture names.

```python
def test_start_session_in_free_launch_auto_runs_to_verdict(_echo_client):
    client, finding_id = _echo_client
    resp = client.post(
        f"/api/findings/{finding_id}/retest-session",
        json={"free_launch": True, "max_steps": 5},
    )
    assert resp.status_code == 202
    session_id = resp.json()["id"]
    # Background tasks run synchronously under Starlette's TestClient, so by the
    # time POST returns the free-launch loop has driven to a verdict.
    got = client.get(f"/api/retest-sessions/{session_id}").json()
    assert got["free_launch"] is True
    assert got["max_steps"] == 5
    assert got["status"] in {"concluded", "given_up"}


def test_free_launch_toggle_endpoint(_echo_client):
    client, finding_id = _echo_client
    session_id = client.post(f"/api/findings/{finding_id}/retest-session").json()["id"]
    resp = client.post(
        f"/api/retest-sessions/{session_id}/free-launch", json={"enabled": True}
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}


def test_get_session_returns_budget_defaults(_echo_client):
    client, finding_id = _echo_client
    session_id = client.post(f"/api/findings/{finding_id}/retest-session").json()["id"]
    got = client.get(f"/api/retest-sessions/{session_id}").json()
    assert got["free_launch"] is False
    assert got["max_steps"] == 8
    assert got["max_seconds"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/integration/test_retest_session_api.py -k "free_launch or budget_defaults" -v`
Expected: FAIL — the request models / route / response fields don't exist yet.

- [ ] **Step 3: Add the request models** (`src/revalid/app.py`, near `MessageRequest`)

```python
class StartSessionRequest(BaseModel):
    """Optional body for starting a retest session: free-launch + budget config (FR-17 Slice 5)."""

    free_launch: bool = False
    max_steps: int = Field(default=8, ge=1)
    max_seconds: int | None = Field(default=None, ge=1)


class FreeLaunchRequest(BaseModel):
    """Body for the live free-launch toggle (FR-17 Slice 5)."""

    enabled: bool
```

- [ ] **Step 4: Accept the body on session start + seed config into the worker** (`src/revalid/app.py`, `start_retest_session`)

```python
    def start_retest_session(
        finding_id: int,
        background: BackgroundTasks,
        session: SessionDep,
        agent: RetestAgentDep,
        make_sandbox: SandboxFactoryDep,
        body: StartSessionRequest | None = None,
    ) -> RetestSessionOut:
        """Open an agentic retest session and schedule its first agent step (FR-17)."""
        cfg = body or StartSessionRequest()
        version = _current_or_404(session, finding_id)
        prompt = _finding_prompt(version.to_domain())
        record = create_session(
            session,
            finding_id=finding_id,
            model=agent_model_name(agent),
            free_launch=cfg.free_launch,
            max_steps=cfg.max_steps,
            max_seconds=cfg.max_seconds,
        )
        background.add_task(
            run_first_step, sessions, registry, record.id, agent, make_sandbox, prompt
        )
        return RetestSessionOut.from_record(record, [])
```

- [ ] **Step 5: `run_first_step` reads config from the record and passes it to `start_and_step`** (`src/revalid/app.py`)

Inside `run_first_step`, replace the `start_and_step(...)` call:

```python
            sandbox = make_sandbox(session_id)
            record = session.get(RetestSessionRecord, session_id)
            free_launch = record.free_launch if record else False
            max_steps = record.max_steps if record else 8
            max_seconds = float(record.max_seconds) if record and record.max_seconds else None
            start_and_step(
                session, registry, session_id, agent, sandbox, prompt,
                max_steps=max_steps, free_launch=free_launch, max_seconds=max_seconds,
            )
```

- [ ] **Step 6: Add the `run_free_launch` worker** (`src/revalid/app.py`, near `run_message`)

```python
def run_free_launch(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    enabled: bool,
) -> None:
    """Toggle free-launch on a session (FR-17 Slice 5 background task).

    Runs in the background because enabling may drive the auto-approve loop
    (successive agent turns). A no-op if the session is no longer live.
    """
    with sessions() as session:
        set_free_launch(session, registry, session_id, enabled)
```

- [ ] **Step 7: Register the route** (`src/revalid/app.py`, in `_register_session_routes`, near the `/message` route)

```python
    @router.post("/retest-sessions/{session_id}/free-launch", status_code=202)
    def set_free_launch_route(
        session_id: int, body: FreeLaunchRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Toggle free-launch mode on a live session (FR-17 Slice 5).

        Enabling auto-approves a pending command and lets the agent's commands
        auto-run (plan changes stay gated); disabling re-arms the per-command
        gate. Runs in the background; a no-op if the session is no longer live.
        """
        background.add_task(run_free_launch, sessions, registry, session_id, body.enabled)
        return {"status": "accepted"}
```

Add `run_free_launch` and `set_free_launch` to the imports from `revalid.retest_session` / the workers block as needed. If adding this route trips the mccabe gate on `_register_session_routes` (it already has ~6 routes), split it the way `_register_session_stream_route` was split — move the toggle into its own small registrar called from `_register_retest_routes`.

- [ ] **Step 8: Run the integration tests + typecheck + complexity**

Run: `uv run pytest tests/integration/test_retest_session_api.py -q && uv run mypy --strict src/revalid/app.py && uv run xenon --max-absolute C src/revalid/app.py`
Expected: PASS, no errors.

- [ ] **Step 9: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(retest): free-launch REST — start-body config + live toggle endpoint (FR-17 Slice 5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Frontend — budget meter, toggle, auto-tag, given-up banner

**Files:**
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/lib/sessionBudget.ts`, `frontend/src/lib/sessionBudget.test.ts`
- Modify: `frontend/src/routes/RetestSession.tsx`
- Test: `frontend/src/routes/RetestSession.test.tsx` (existing)

**Interfaces:**
- Consumes: `getRetestSession` (extended), `SessionEvent[]` from `useRetestSession`.
- Produces: `RetestSession` interface fields `free_launch`, `max_steps`, `max_seconds`; `setFreeLaunch(id, enabled) => Promise<{status:string}>`; `startRetestSession(findingId, opts?)`; pure helpers `stepsUsed(events)`, `currentFreeLaunch(events, initial)`, `budgetLabel(used, max)`, `givenUpReason(events)`.

- [ ] **Step 1: Extend the API client** (`frontend/src/api/client.ts`)

In the `RetestSession` interface add:

```typescript
  free_launch: boolean;
  max_steps: number;
  max_seconds: number | null;
```

Add optional start opts and the toggle call:

```typescript
export function startRetestSession(
  findingId: number,
  opts?: { free_launch?: boolean; max_steps?: number; max_seconds?: number | null },
): Promise<RetestSession> {
  return request<RetestSession>(`/findings/${String(findingId)}/retest-session`, {
    method: "POST",
    body: opts ? JSON.stringify(opts) : undefined,
  });
}

export function setFreeLaunch(id: number, enabled: boolean): Promise<{ status: string }> {
  return request<{ status: string }>(`/retest-sessions/${String(id)}/free-launch`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}
```

> Check `request`'s signature in this file — if it already sets `Content-Type: application/json` for a `body`, the above is correct; otherwise mirror how `submitMessage` sets headers.

- [ ] **Step 2: Write the failing pure-helper tests** (`frontend/src/lib/sessionBudget.test.ts`)

```typescript
import { describe, expect, it } from "vitest";

import { budgetLabel, currentFreeLaunch, givenUpReason, stepsUsed } from "./sessionBudget";
import type { SessionEvent } from "../api/client";

const ev = (kind: string, payload: Record<string, unknown> = {}, seq = 0): SessionEvent =>
  ({ seq, kind, payload }) as SessionEvent;

describe("stepsUsed", () => {
  it("counts command_approved events", () => {
    expect(stepsUsed([ev("command_approved"), ev("command_output"), ev("command_approved")])).toBe(2);
  });
  it("is zero with none", () => {
    expect(stepsUsed([ev("command_proposed")])).toBe(0);
  });
});

describe("currentFreeLaunch", () => {
  it("follows the latest free_launch_changed event", () => {
    const events = [ev("free_launch_changed", { enabled: true }), ev("free_launch_changed", { enabled: false })];
    expect(currentFreeLaunch(events, false)).toBe(false);
  });
  it("falls back to the initial value with no toggle events", () => {
    expect(currentFreeLaunch([ev("command_approved")], true)).toBe(true);
  });
});

describe("budgetLabel", () => {
  it("formats used / max", () => {
    expect(budgetLabel(3, 8)).toBe("3 / 8 steps");
  });
});

describe("givenUpReason", () => {
  it("returns the verdict rationale of a given-up session", () => {
    expect(givenUpReason([ev("verdict", { status: "inconclusive", rationale: "budget exhausted" })])).toBe(
      "budget exhausted",
    );
  });
  it("returns null when no verdict is present", () => {
    expect(givenUpReason([ev("command_output")])).toBeNull();
  });
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd frontend && npx vitest run src/lib/sessionBudget.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the pure helpers** (`frontend/src/lib/sessionBudget.ts`)

```typescript
import type { SessionEvent } from "../api/client";

/** Steps used so far = the number of approved commands (human or auto). */
export function stepsUsed(events: SessionEvent[]): number {
  return events.filter((e) => e.kind === "command_approved").length;
}

/**
 * The session's current free-launch state: the latest `free_launch_changed`
 * event's `enabled`, or the session's initial value if it was never toggled.
 */
export function currentFreeLaunch(events: SessionEvent[], initial: boolean): boolean {
  const latest = [...events].reverse().find((e) => e.kind === "free_launch_changed");
  return latest ? Boolean(latest.payload.enabled) : initial;
}

/** "3 / 8 steps" — the step-budget meter label. */
export function budgetLabel(used: number, max: number): string {
  return `${String(used)} / ${String(max)} steps`;
}

/** The rationale of a given-up session's verdict, or null if none recorded. */
export function givenUpReason(events: SessionEvent[]): string | null {
  const verdict = [...events].reverse().find((e) => e.kind === "verdict");
  return verdict ? String(verdict.payload.rationale ?? "") || null : null;
}
```

- [ ] **Step 5: Run the helper tests**

Run: `cd frontend && npx vitest run src/lib/sessionBudget.test.ts`
Expected: PASS.

- [ ] **Step 6: Wire the UI** (`frontend/src/routes/RetestSession.tsx`). Fetch the record for config via TanStack Query (config fields the WS stream doesn't carry), derive the live values from events, and render three additions: the meter + toggle in the header, the auto-tag on auto-approved command cards, and the given-up banner. Import `useQuery` from `@tanstack/react-query` and `getRetestSession`, `setFreeLaunch` from the client, plus the helpers.

Add near the top of the component body (after the existing `useRetestSession` call):

```tsx
  const { data: record } = useQuery({
    queryKey: ["retest-session", id],
    queryFn: () => getRetestSession(id),
  });
  const freeLaunch = currentFreeLaunch(events, record?.free_launch ?? false);
  const used = stepsUsed(events);
  const maxSteps = record?.max_steps ?? 8;

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => setFreeLaunch(id, enabled),
  });
```

Render in the session-controls row (beside **End session**), disabled once the session is `OVER_STATUSES`:

```tsx
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={freeLaunch}
            disabled={isOver || toggle.isPending}
            onChange={(e) => toggle.mutate(e.target.checked)}
          />
          Free-launch
        </label>
        <span className="text-sm text-muted" aria-label="step budget">
          {budgetLabel(used, maxSteps)}
        </span>
```

For the auto-tag: where command approval cards render, an approval whose `command_approved` payload has `auto === true` shows a subtle "auto" chip instead of approve/reject controls. For the given-up banner: when `status === "given_up"`, render a distinct banner citing `givenUpReason(events)` (e.g. `Agent gave up — {reason}`), separate from the `concluded`/`ended` treatment.

> The exact JSX must match the file's existing card/banner structure — follow the patterns already used for the verdict banner and command cards. `isOver` is the existing `OVER_STATUSES.has(status)` check (or add it if only the set exists).

- [ ] **Step 7: Extend the component test** (`frontend/src/routes/RetestSession.test.tsx`) — assert the meter renders "N / M steps", the toggle calls `setFreeLaunch`, an auto-approved command shows the auto tag (no approve button), and a `given_up` status renders the given-up banner. Follow the existing test's fake-socket + rendered-events setup; mock `setFreeLaunch`/`getRetestSession` the way the file already mocks client calls.

- [ ] **Step 8: Run the frontend gates**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src && npx vite build`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/lib/sessionBudget.ts frontend/src/lib/sessionBudget.test.ts frontend/src/routes/RetestSession.tsx frontend/src/routes/RetestSession.test.tsx
git commit -m "feat(ui): free-launch toggle + budget meter + given-up banner (FR-17 Slice 5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Docs — ADR-0029, SRS AC9–AC12, roadmap

**Files:**
- Create: `docs/adr/0029-agentic-retest-free-launch.md`
- Modify: `docs/requirements/srs.md` (FR-17 acceptance criteria)
- Modify: `docs/roadmap.md` (M6 Slice 5 checkbox + state note)

**Interfaces:** none (documentation).

- [ ] **Step 1: Write ADR-0029** using the `adr` skill format (MADR). Status `proposed`. Record: free-launch as gate-reuse (not a forked path); plan-changes-always-gated; the two budget bounds (step in both modes, wall-clock free-launch-only, checked at step boundaries); auto-approvals transcript-marked `{"auto": true}` and the NFR-02 consequence (the transcript still shows exactly what ran under which mode). Alternatives considered: (a) a forked auto-execute path (rejected — two places a command runs, audit divergence); (b) recursion through `apply_decision` (rejected — stack depth at large budgets); (c) wall-clock in both modes (rejected — human think-time trips it falsely). Add the References section (design spec, epic #87, FR-17, ADR-0025/0027) for FR-17-family consistency.

- [ ] **Step 2: Add FR-17 Slice 5 acceptance criteria** to `docs/requirements/srs.md`, after the Slice 4 block, mirroring its structure:

```markdown
- **Acceptance criteria — Slice 5** (met — issue #100, ADR-0029 proposed, 2026-07-17):
  - [x] **AC9**: with free-launch on, the agent's commands auto-run to a verdict with no per-command human approval, while a `set_plan` proposal still pauses for approval (plan changes are always gated).
  - [x] **AC10**: free-launch is settable at session start (`POST /retest-session` body) and toggleable live (`POST /retest-sessions/{id}/free-launch`); enabling mid-session auto-approves any pending command; every toggle is a `free_launch_changed` transcript event and each auto-approval is marked `{"auto": true}`.
  - [x] **AC11**: `max_steps` (both modes) and `max_seconds` (free-launch only, checked at step boundaries) force-conclude the session `given_up`/`inconclusive` with a budget-exhausted reason; both bounds are visible in the SPA.
  - [x] **AC12**: the given-up state renders distinctly from an operator-ended or concluded session.
```

Update the FR-17 "Deferred to later slices" line to drop Slice 5 (leaving only Slice 6 — verdict adjudication + FR-09/10/12 integration + retiring the batch path).

- [ ] **Step 3: Update the roadmap** — tick the M6 Slice 5 checkbox and add a dated state note (2026-07-17) summarizing what landed (free-launch gate-reuse, budgets, toggle, UI), mirroring the Slice-4 entry's style. Note the old batch path still retires in Slice 6.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0029-agentic-retest-free-launch.md docs/requirements/srs.md docs/roadmap.md
git commit -m "docs(retest): ADR-0029 free-launch; SRS AC9-AC12 + roadmap (FR-17 Slice 5, #100)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (before opening the PR)

- [ ] **Full backend suite + coverage:** `uv run pytest -q` and `uv run pytest --cov=src/revalid --cov-report=term-missing tests/unit tests/integration` — coverage ≥ 80% on `src/`; `retest_session.py`'s new lines covered (live Docker lines excluded).
- [ ] **All gates:** `uv run mypy --strict src tests && uv run ruff check src tests && uv run ruff format --check src tests && uv run xenon --max-absolute C src`.
- [ ] **Frontend gates:** `cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src && npx vite build`.
- [ ] **Live smoke (the real proof):** with a local Ollama + the Juice Shop lab up, start a session with `{"free_launch": true, "max_steps": 4}` on a real finding, watch commands auto-run in the console to a verdict with no clicks; then start a gated session, let it propose a command, flip the toggle on, and confirm the pending command auto-approves. Confirm a low `max_steps` force-concludes `given_up` with a distinct banner. (`make demo-retest-session` still passes unchanged — the scripted demo path does not set free-launch.)
- [ ] **Open the PR** with `Closes #100`, the FR-17 Slice 5 acceptance criteria as the "How to validate" checklist, and the exact commands above. Queue auto-merge once required CI is green (per CLAUDE.md); Álvaro reviews async.
