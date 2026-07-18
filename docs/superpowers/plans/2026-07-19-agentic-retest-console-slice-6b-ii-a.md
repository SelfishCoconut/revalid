# FR-17 Slice 6b-ii-a — Remove `set_plan` (agent no longer proposes the plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every code task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the agent's `set_plan` proposal mechanism (Slice 3) so the guiding plan is no longer agent-owned — a pure teardown that clears the way for the user-owned goal (6b-ii-b). The deferred-tool gate now only ever carries a `run_command`, so the orchestrator's command/plan split collapses to a command-only path.

**Architecture:** Remove the gated `set_plan` tool + its `emit_plan` dep from the agent; collapse the orchestrator's `pending_kind` split, the `awaiting_plan` transient, and the plan-approval events; drop the SPA plan-proposal card. Keep `PLAN_UPDATED` + the plan panel (they become the user's goal surface in 6b-ii-b) — but nothing writes `plan_updated` after this slice, so the panel is empty until 6b-ii-b.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic AI (FunctionModel in tests), pytest; React/TS/Vite, vitest.

## Global Constraints

- Python 3.12+, `uv`; `mypy --strict`, ruff (line 100, Google docstrings), xenon max absolute **C**.
- Coverage ≥ 80% on `src/`; Conventional Commits + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Frontend gates: eslint + tsc + build + vitest green; owned pure logic stays pinned.
- Command gating, free-launch, egress lock (NFR-03), single-user model (ADR-0008) unchanged.
- Branch: `feat/fr17-user-goal-slice6b-ii` (shared with 6b-ii-b). PR body: `Closes #107` is deferred to the final 6b-ii PR; this PR references #107 (`Part of #107`) — the card advances when 6b-ii-b closes it.

---

## File Structure

- `src/revalid/retest_agent.py` — remove the `set_plan` tool, `emit_plan`/`_no_emit_plan`, the plan bullet in the instructions.
- `src/revalid/retest_session.py` — remove `LiveSession.pending_kind`, the `set_plan` branch in `_emit_proposal`, the `pending_kind` arg of `_decision_event_kind`, the plan transient/exempt logic in `_resume_with_decision`, the plan guard in `_drive_auto`, the `emit_plan` closure in `_make_deps`.
- `src/revalid/domain.py` — remove `RetestSessionStatus.AWAITING_PLAN` and `SessionEventKind.PLAN_PROPOSED/PLAN_APPROVED/PLAN_REJECTED`; keep `PLAN_UPDATED`.
- `tests/_retest_helpers.py` — remove `script_plan_then_run_then_conclude`.
- `tests/unit/test_retest_session.py`, `tests/integration/test_retest_session_api.py` — remove the plan-flow tests.
- `frontend/src/routes/RetestSession.tsx` + `.test.tsx` — remove the `plan_proposed` card + `awaiting_plan` handling.

---

## Task 1: Remove `set_plan` from the agent

**Files:** Modify `src/revalid/retest_agent.py`; Modify `tests/_retest_helpers.py`, `tests/unit/test_retest_agent.py` (whichever reference `set_plan`).

- [ ] **Step 1: Find the agent tests that assert `set_plan`**

Run: `grep -rn "set_plan" tests/unit/test_retest_agent.py tests/_retest_helpers.py`
Note each; they must be updated/removed in this task.

- [ ] **Step 2: Write/adjust the failing test — the agent exposes no `set_plan` tool**

In `tests/unit/test_retest_agent.py` add:

```python
def test_agent_has_no_set_plan_tool() -> None:
    agent = build_retest_agent(FunctionModel(lambda m, i: ModelResponse(parts=[])))
    tool_names = set(agent._function_toolset.tools)  # pydantic-ai tool registry
    assert "set_plan" not in tool_names
    assert "run_command" in tool_names
```

(If `agent._function_toolset.tools` is not the right accessor in this pydantic-ai version, discover it: `python -c "from revalid.retest_agent import build_retest_agent; a=build_retest_agent('test'); print(dir(a))"` and use the tool-name registry it exposes.)

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_retest_agent.py::test_agent_has_no_set_plan_tool -q`
Expected: FAIL — `set_plan` is still registered.

- [ ] **Step 4: Remove the tool + dep + instructions**

In `src/revalid/retest_agent.py`:
- Delete the entire `@agent.tool(requires_approval=True)` `set_plan` function (the `def set_plan(...)` block).
- Delete `_no_emit_plan` and the `emit_plan: Callable[[list[str]], None] = _no_emit_plan` field from `RetestSessionDeps` (and its doc comment).
- In `_INSTRUCTIONS`, delete the bullet beginning "FIRST, propose a short guiding plan with `set_plan`..." (through "...approved the same way.").
- Update `build_retest_agent`'s docstring: "gated ``run_command`` tool + a verdict" (drop `set_plan`).

- [ ] **Step 5: Remove the plan script helper**

In `tests/_retest_helpers.py` delete `script_plan_then_run_then_conclude` (and any now-unused imports it alone used).

- [ ] **Step 6: Run the agent tests + gates**

Run: `uv run pytest tests/unit/test_retest_agent.py -q && uv run mypy --strict src/revalid/retest_agent.py && uv run ruff check src/revalid/retest_agent.py`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/revalid/retest_agent.py tests/unit/test_retest_agent.py tests/_retest_helpers.py
git commit -m "refactor(retest): remove the set_plan tool — the agent no longer proposes the plan (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Collapse the orchestrator's plan path

**Files:** Modify `src/revalid/retest_session.py`; Modify `tests/unit/test_retest_session.py`.

- [ ] **Step 1: Remove the obsolete plan-flow unit tests**

In `tests/unit/test_retest_session.py`, delete `test_free_launch_still_gates_plan_changes` and any other test that imports/uses `script_plan_then_run_then_conclude` or asserts `awaiting_plan`/`plan_proposed`. Run `grep -n "plan_then\|awaiting_plan\|plan_proposed\|set_plan\|pending_kind" tests/unit/test_retest_session.py` and remove each.

- [ ] **Step 2: Simplify `_make_deps`** — delete the `emit_plan` closure and drop `emit_plan=emit_plan` from the `RetestSessionDeps(...)` construction.

- [ ] **Step 3: Simplify `_emit_proposal`** — replace the whole function with the command-only form:

```python
def _emit_proposal(
    session: Session, session_id: int, live: LiveSession, call: Any
) -> RetestSessionStatus:
    """Record the agent's proposed command and return the awaiting status.

    The gate now only ever carries a ``run_command`` (the agent's ``set_plan``
    was removed in 6b-ii): a proposal is always a command awaiting human approval.
    """
    args = call.args_as_dict()
    live.pending_call_id = call.tool_call_id
    append_event(
        session,
        session_id,
        SessionEventKind.COMMAND_PROPOSED,
        {
            "command": args["command"],
            "rationale": args["rationale"],
            "tool_call_id": call.tool_call_id,
        },
    )
    return RetestSessionStatus.AWAITING_COMMAND
```

- [ ] **Step 4: Remove `LiveSession.pending_kind`** — delete the `pending_kind: str = "command"` field and its doc comment from the `LiveSession` dataclass.

- [ ] **Step 5: Simplify `_decision_event_kind`** — replace with:

```python
def _decision_event_kind(*, approved: bool) -> SessionEventKind:
    """Map a command decision to its transcript event kind."""
    return SessionEventKind.COMMAND_APPROVED if approved else SessionEventKind.COMMAND_REJECTED
```

- [ ] **Step 6: Simplify `_resume_with_decision`** — every pending call is a command now. Replace the `is_command`/transient/plan-exempt block. The budget check + transient status become unconditional:

```python
    if approved and _step_budget_exhausted(live):
        _give_up(session, registry, session_id, "budget exhausted")
        return

    set_status(session, session_id, RetestSessionStatus.RUNNING_COMMAND)
```

(delete the `is_command = live.pending_kind == "command"` line and the `transient = ... if is_command else ...` ternary; keep the rest of the function — the `DeferredToolResults`, drain, resume, `_dispatch_output` — unchanged.)

- [ ] **Step 7: Simplify `_drive_auto`** — in the guard `if (live is None or not live.free_launch or live.pending_kind != "command" or live.pending_call_id is None):`, delete the `or live.pending_kind != "command"` clause. Update the function's docstring to drop the "proposes a `set_plan`" stop condition.

- [ ] **Step 8: Update `apply_decision`** — change `kind = _decision_event_kind(live.pending_kind, approved=approved)` to `kind = _decision_event_kind(approved=approved)`.

- [ ] **Step 9: Run the session tests + gates**

Run: `uv run pytest tests/unit/test_retest_session.py -q && uv run mypy --strict src/revalid/retest_session.py && uv run ruff check src/revalid/retest_session.py && uv run xenon --max-absolute C src/revalid/retest_session.py`
Expected: PASS (command/free-launch/message/verdict/adjudicate tests unaffected); mypy + ruff + xenon clean.

- [ ] **Step 10: Commit**

```bash
git add src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "refactor(retest): collapse the orchestrator's plan-proposal path to command-only (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Remove the unused plan enum values

**Files:** Modify `src/revalid/domain.py`; Modify `tests/unit/test_domain.py` (if it references them).

- [ ] **Step 1: Confirm nothing still references them**

Run: `grep -rn "AWAITING_PLAN\|PLAN_PROPOSED\|PLAN_APPROVED\|PLAN_REJECTED\|awaiting_plan\|plan_proposed\|plan_approved\|plan_rejected" src/ tests/ frontend/src`
Expected after Tasks 1–2 + 4: only this task's targets remain (backend). If any `src/` reference remains, it was missed — fix it first.

- [ ] **Step 2: Remove the values**

In `src/revalid/domain.py`:
- From `RetestSessionStatus`, delete `AWAITING_PLAN = "awaiting_plan"`.
- From `SessionEventKind`, delete `PLAN_PROPOSED`, `PLAN_APPROVED`, `PLAN_REJECTED`. **Keep** `PLAN_UPDATED` (the goal's event, used by 6b-ii-b) and `PLAN_APPROVED`? No — delete `PLAN_APPROVED`/`PLAN_REJECTED`/`PLAN_PROPOSED`, keep only `PLAN_UPDATED`.

- [ ] **Step 3: Run the domain + full backend tests**

Run: `uv run pytest tests/unit/test_domain.py tests/unit tests/integration -q && uv run mypy --strict src/revalid/domain.py`
Expected: PASS; mypy clean.

- [ ] **Step 4: Commit**

```bash
git add src/revalid/domain.py tests/unit/test_domain.py
git commit -m "refactor(domain): drop the unused plan-proposal states/events (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Frontend — remove the plan-proposal card

**Files:** Modify `frontend/src/routes/RetestSession.tsx` + `frontend/src/routes/RetestSession.test.tsx`.

- [ ] **Step 1: Update the failing test**

In `RetestSession.test.tsx`, find the test using `kind: "plan_proposed"` / `status: "awaiting_plan"` (~line 429). Replace it with a test asserting a plan proposal is no longer treated as an approval card — or delete it if it only exercised the removed path. Keep the command-approval tests.

- [ ] **Step 2: Run it to verify it fails / is red**

Run: `cd frontend && npx vitest run src/routes/RetestSession.test.tsx`
Expected: the plan-proposal test fails against the not-yet-updated component (or you've removed it — then this step confirms the suite is green after removal).

- [ ] **Step 3: Remove the plan-proposal handling in the component**

In `frontend/src/routes/RetestSession.tsx`:
- In `latestProposal`, drop `|| event.kind === "plan_proposed"` — only `command_proposed` is a proposal.
- In `awaitingApproval`, drop `|| status === "awaiting_plan"`.
- In the chat-item render (~line 327), delete the `if (event.kind === "plan_proposed") { ... }` block that renders the "Proposed plan"/"Plan revision" approval card.
- Leave the **panel** (`planSteps` / `currentPlan` / the `plan_updated` StepList) in place — it becomes the goal panel in 6b-ii-b (it renders empty when there are no `plan_updated` events).

- [ ] **Step 4: Run the frontend gates**

Run: `cd frontend && npx vitest run --coverage && npx tsc --noEmit && npx eslint src && npm run build`
Expected: all green; coverage floor met.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/RetestSession.tsx frontend/src/routes/RetestSession.test.tsx
git commit -m "refactor(ui): remove the agent plan-proposal card (FR-17 6b-ii)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: ADR-0032 + roadmap + full verification + PR

- [ ] **Step 1:** Write `docs/adr/0032-user-owned-goal.md` (proposed) via the `adr` skill (or match ADR-0031's format), covering the **whole** 6b-ii decision — the plan becomes a user-owned goal (generic `generate_goal`, seed at start, user edit/regenerate, pure-queue injection) and the agent's `set_plan` is removed — noting 6b-ii-a implements the teardown and 6b-ii-b the goal. Add the row to `docs/adr/README.md`.

- [ ] **Step 2:** Add a roadmap note under the M6 Slice 6b entry: 6b-ii split into **6b-ii-a** (set_plan removed — this PR) and **6b-ii-b** (user goal).

- [ ] **Step 3: Full gate**

Run: `uv run pytest tests/unit tests/integration -q && uv run pytest --cov=src/revalid -q && uv run mypy --strict src tests && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run xenon --max-absolute C src`
Expected: all green; coverage ≥ 80%.

Run: `cd frontend && npx tsc --noEmit && npx eslint src && npx vitest run --coverage && npm run build`
Expected: all green.

Run: `make demo-retest-session`
Expected: succeeds (propose → approve → output → verdict; no plan step).

- [ ] **Step 4: Push + PR**

```bash
git add docs/
git commit -m "docs(retest): ADR-0032 + roadmap for the user-owned goal teardown (FR-17 6b-ii-a)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git push -u origin feat/fr17-user-goal-slice6b-ii
```

Open the PR titled "FR-17 Slice 6b-ii-a: remove set_plan (agent no longer proposes the plan)" with a filled "How to validate"; body says **`Part of #107`** (NOT `Closes` — 6b-ii-b closes it). Queue squash auto-merge; monitor CI to green.

---

## Self-Review (completed during authoring)

- **Spec coverage:** §2.5 (remove `set_plan`) → Tasks 1–4; §4 AC3 (agent no longer proposes; `awaiting_plan`/plan events gone) → Tasks 1–3. The goal §2.1–2.4/§2.6 + AC1–2 are **6b-ii-b** (deliberately out of this plan). `PLAN_UPDATED` kept for 6b-ii-b.
- **Placeholder scan:** none — removals give exact targets; replacements show full code.
- **Type consistency:** `_decision_event_kind(*, approved)` and `_emit_proposal(...)` signatures updated at every call site (`apply_decision`, `_drive_auto`); `pending_kind` removed everywhere it's read.
