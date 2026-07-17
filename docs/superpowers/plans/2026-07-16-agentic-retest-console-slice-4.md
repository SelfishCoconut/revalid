# FR-17 Slice 4 — chat input / steering & Q&A — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator type free-text messages into the retest console to steer the agent and ask it questions; the agent reads them (queued, delivered on its next approve/reject) and can answer in prose.

**Architecture:** The agent is never idle, so a message can only be delivered at the next turn boundary — the next approve/reject. Queued messages are drained and passed as a first-class `user_prompt` **alongside** `deferred_tool_results` on the resume (verified against Pydantic AI docs). A new non-gated `respond` tool lets the agent answer in prose (an `agent_message` event) mid-run, so the run continues to its next proposal/verdict. `!`-command observations (Slice 2) are untouched — they stay folded into the tool result; chat messages are the operator's *voice* (a user turn).

**Tech Stack:** Python 3.12 / Pydantic AI / FastAPI / SQLAlchemy (backend); React 19 / TanStack Query / Vitest (frontend). Design spec: `docs/superpowers/specs/2026-07-16-agentic-retest-console-slice-4-design.md`. Issue: #96. Proposes ADR-0028.

## Global Constraints

- Python 3.12+, managed with `uv`; run tools via `uv run` / `make`.
- `mypy --strict` must pass (`make typecheck`); Ruff lint + format, line length 100, Google docstrings on public API (`make lint`).
- Complexity gate: `uv run xenon --max-absolute C --max-modules B --max-average A src` — refactor if tripped, never suppress.
- Tests per pyramid level: `tests/unit/` (no I/O, LLM via Pydantic AI `FunctionModel`), `tests/integration/` (marker `integration`, real REST + fakes). Coverage ≥ 80% on `src/`.
- Frontend: `npm --prefix frontend run test` (vitest), `... run lint` (eslint), `... run build` (tsc + vite).
- Conventional Commits; every commit carries `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Reuse before adding: `!`-command observation path (`observations`/`observe`/`drain`, `format_observations`) stays as-is; mirror it, don't reshape it.

## File Structure

- `src/revalid/retest_agent.py` — **modify**: add `emit_message` dep + non-gated `respond` tool + instructions.
- `src/revalid/domain.py` — **modify**: add `SessionEventKind.HUMAN_MESSAGE`.
- `src/revalid/retest_session.py` — **modify**: `LiveSession` message buffer + `submit_message` + `_make_deps` wiring + `_resume_with_decision` delivers `user_prompt`.
- `src/revalid/app.py` — **modify**: `MessageRequest` model + `run_message` task + `POST /message` route.
- `tests/_retest_helpers.py` — **modify**: `script_respond_then_conclude`, `script_run_then_conclude_noting_message`, `operator_message_count`.
- `tests/unit/test_retest_agent.py`, `tests/unit/test_retest_session.py`, `tests/integration/test_retest_session_api.py` — **modify**: new tests.
- `frontend/src/api/client.ts` — **modify**: `submitMessage`.
- `frontend/src/routes/RetestSession.tsx` — **modify**: input wiring + `HumanTurn` + queue UX.
- `frontend/src/routes/RetestSession.test.tsx` — **modify**: chat-message tests.
- `docs/adr/0028-agentic-retest-chat-steering.md` (+ `docs/adr/README.md`), `docs/requirements/srs.md`, `docs/roadmap.md` — **modify/create**: ADR + AC + state.

---

### Task 1: `respond` tool for agent prose (Q&A)

**Files:**
- Modify: `src/revalid/retest_agent.py`
- Modify: `tests/_retest_helpers.py`
- Test: `tests/unit/test_retest_agent.py`

**Interfaces:**
- Produces: `RetestSessionDeps.emit_message: Callable[[str], None]` (default no-op `_no_emit_message`); a non-gated `respond(ctx, message: str) -> str` tool on the built agent that calls `ctx.deps.emit_message(message)`.
- Produces (test helper): `script_respond_then_conclude(messages, info) -> ModelResponse`.
- Consumes: existing `has_tool_result`, `RetestSessionDeps`, `build_retest_agent`, `ConcludeOutput`, `FakeSandbox`.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_retest_agent.py` (add `script_respond_then_conclude` to the `tests._retest_helpers` import):

```python
def test_respond_tool_emits_agent_message_and_run_continues() -> None:
    """The non-gated respond tool emits prose mid-run; the run then reaches a verdict."""
    box = FakeSandbox([])  # respond never touches the sandbox
    prose: list[str] = []
    deps = RetestSessionDeps(
        sandbox=box, emit_output=lambda *_: None, emit_message=prose.append
    )
    agent = build_retest_agent(FunctionModel(script_respond_then_conclude))

    result = agent.run_sync("Retest the SQLi finding.", deps=deps)

    assert prose == ["the 500 was the WAF rejecting the payload"]
    assert isinstance(result.output, ConcludeOutput)
    assert box.commands == []
```

- [ ] **Step 2: Add the test helper** — append to `tests/_retest_helpers.py`:

```python
def script_respond_then_conclude(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stateful scripted model: call ``respond`` once, then conclude (FR-17 Slice 4).

    Proves the non-gated ``respond`` tool emits prose mid-run and the run then
    continues to a verdict without proposing any command.
    """
    if not has_tool_result(messages, "respond"):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="respond",
                    args={"message": "the 500 was the WAF rejecting the payload"},
                )
            ]
        )
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={"status": "inconclusive", "rationale": "answered the operator"},
            )
        ]
    )
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_retest_agent.py::test_respond_tool_emits_agent_message_and_run_continues -v`
Expected: FAIL — `RetestSessionDeps` has no `emit_message` (TypeError) / no `respond` tool.

- [ ] **Step 4: Implement** in `src/revalid/retest_agent.py`.

(a) After `_no_emit_plan` (near line 56) add:

```python
def _no_emit_message(message: str) -> None:
    """Default ``emit_message``: drop agent prose (agent-unit tests need no sink)."""
```

(b) In `RetestSessionDeps`, after the `emit_plan` field, add:

```python
    #: Records the agent's prose replies to the operator (FR-17 Slice 4). Invoked
    #: by the non-gated ``respond`` tool; the orchestrator wires this to append an
    #: ``agent_message`` transcript event. The default drops it (agent-unit tests).
    emit_message: Callable[[str], None] = _no_emit_message
```

(c) In `build_retest_agent`, after the `set_plan` tool, add:

```python
    @agent.tool
    def respond(ctx: RunContext[RetestSessionDeps], message: str) -> str:
        """Send a short prose message to the operator (e.g. answer a question).

        Use this to reply to the operator or give a brief status note — not to
        narrate every step. It runs nothing; after it you continue with your
        plan, a command, or a verdict.

        Args:
            ctx: The run context carrying the message-emit callback.
            message: The prose to show the operator in the chat.

        Returns:
            A short confirmation the message was delivered.
        """
        ctx.deps.emit_message(message)
        return "Delivered to the operator."
```

(d) In `_INSTRUCTIONS`, before the closing `"""`, append this rule (keep the `\`-continuation style):

```python
- The operator may message you at any time; their message arrives as a new \
turn. Always address it: answer questions with `respond`, then continue; fold \
any steering into your plan and commands. Use `respond` sparingly — to answer \
or for a brief status note, not to narrate every step.
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_retest_agent.py -v`
Expected: PASS (new test + the two existing gate tests).

- [ ] **Step 6: Lint + typecheck**

Run: `make lint && make typecheck`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/revalid/retest_agent.py tests/_retest_helpers.py tests/unit/test_retest_agent.py
git commit -m "feat(retest): non-gated respond tool for agent prose / Q&A (FR-17 Slice 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: message buffer + queue delivery in the orchestrator

**Files:**
- Modify: `src/revalid/domain.py`
- Modify: `src/revalid/retest_session.py`
- Modify: `tests/_retest_helpers.py`
- Test: `tests/unit/test_retest_session.py`

**Interfaces:**
- Consumes: Task 1's `emit_message` dep; existing `LiveSession`, `_make_deps`, `_resume_with_decision`, `append_event`, `SessionRegistry`.
- Produces: `SessionEventKind.HUMAN_MESSAGE = "human_message"`; `LiveSession.human_messages: list[str]` + `receive_message(text)` + `drain_messages() -> list[str]`; `submit_message(session, registry, session_id, text) -> None`; `_resume_with_decision` now passes a `user_prompt` when messages are queued; `_make_deps` wires `emit_message` to an `AGENT_MESSAGE` event.
- Produces (test helpers): `operator_message_count(messages) -> int`, `script_run_then_conclude_noting_message(messages, info) -> ModelResponse`.

- [ ] **Step 1: Add `HUMAN_MESSAGE` to the domain enum** — in `src/revalid/domain.py`, in `SessionEventKind`, after `HUMAN_COMMAND = "human_command"`:

```python
    HUMAN_MESSAGE = "human_message"
```

- [ ] **Step 2: Write the failing tests** — append to `tests/unit/test_retest_session.py` (extend the `tests._retest_helpers` import with `operator_message_count` is not needed there; add `script_run_then_conclude_noting_message` to it):

```python
def test_submit_message_records_event_and_buffers() -> None:
    """A chat message is recorded on the transcript and queued on the live session."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(FunctionModel(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_message(session, registry, s.id, "focus on the login endpoint")

        msgs = [e for e in rs.load_events_after(session, s.id, 0) if e["kind"] == "human_message"]
        assert len(msgs) == 1
        assert msgs[0]["payload"]["text"] == "focus on the login endpoint"
        live = registry.get(s.id)
        assert live is not None
        assert live.human_messages == ["focus on the login endpoint"]


def test_submit_message_on_dead_session_is_a_noop() -> None:
    """A chat message to a non-live session records nothing and does not raise."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        rs.submit_message(session, registry, 999, "hello")  # never started
        assert rs.load_events_after(session, 999, 0) == []


def test_agent_reads_queued_message_on_approve() -> None:
    """A queued chat message reaches the agent as a user turn on the next approval."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(FunctionModel(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_message(session, registry, s.id, "focus on the login endpoint")
        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)

        session.refresh(s)
    assert s.verdict_rationale == "saw-message"


def test_agent_reads_queued_message_on_reject() -> None:
    """A queued chat message is delivered even when the pending command is rejected."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(FunctionModel(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_message(session, registry, s.id, "focus on the login endpoint")
        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=False, reason="no", command_id=cid)

        session.refresh(s)
    assert s.verdict_rationale == "saw-message"


def test_no_message_means_no_extra_user_turn() -> None:
    """Without a chat message the agent sees only the initial goal (control)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(FunctionModel(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)

        session.refresh(s)
    assert s.verdict_rationale == "no-message"
```

- [ ] **Step 3: Add the test helpers** — append to `tests/_retest_helpers.py` and extend its `pydantic_ai.messages` import with `UserPromptPart`:

```python
def operator_message_count(messages: list[ModelMessage]) -> int:
    """Count user-turn messages in history.

    A retest starts with exactly one user turn (the finding goal); each operator
    chat message delivered on an approve/reject resume adds one more.
    """
    return sum(
        1
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, UserPromptPart)
    )


def script_run_then_conclude_noting_message(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    """Propose a command, then conclude reporting whether an operator chat message arrived.

    The verdict rationale is ``"saw-message"`` iff more than one user turn is
    present (the initial goal plus a delivered chat message), else ``"no-message"``.
    """
    if not has_command_result(messages):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_command",
                    args={"command": "curl -s http://revalid-juice-shop:3000/", "rationale": "probe"},
                )
            ]
        )
    rationale = "saw-message" if operator_message_count(messages) > 1 else "no-message"
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={"status": "still_open", "rationale": rationale},
            )
        ]
    )
```

The import line in `tests/_retest_helpers.py` becomes:

```python
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_retest_session.py -k "message" -v`
Expected: FAIL — `submit_message` / `human_messages` don't exist.

- [ ] **Step 5: Implement in `src/revalid/retest_session.py`.**

(a) In `LiveSession`, after the `observations` field + `observe`/`drain` methods, add:

```python
    #: Free-text operator chat messages (FR-17 Slice 4) queued since the agent's
    #: last turn. Delivered as a first-class ``user_prompt`` on the next agent
    #: resume (approve/reject) — the operator's *voice*, distinct from
    #: ``observations`` (`!` command *results* folded into a tool return).
    #: Guarded by ``lock`` — appended by the message worker, drained by the next
    #: agent resume, on separate threads.
    human_messages: list[str] = field(default_factory=list)

    def receive_message(self, text: str) -> None:
        """Queue one operator chat message for the agent's next turn (thread-safe)."""
        with self.lock:
            self.human_messages.append(text)

    def drain_messages(self) -> list[str]:
        """Atomically return and clear the queued operator chat messages (thread-safe)."""
        with self.lock:
            drained = list(self.human_messages)
            self.human_messages.clear()
            return drained
```

(b) In `_make_deps`, add an `emit_message` closure next to `emit_plan` and pass it into `RetestSessionDeps`:

```python
    def emit_message(message: str) -> None:
        append_event(session, session_id, SessionEventKind.AGENT_MESSAGE, {"text": message})

    return RetestSessionDeps(
        sandbox=live.sandbox,
        emit_output=emit,
        drain_observations=live.drain,
        emit_plan=emit_plan,
        emit_message=emit_message,
    )
```

(c) In `_resume_with_decision`, replace the `run_sync` call so a queued message is delivered as `user_prompt`. Change:

```python
    deps = _make_deps(session, session_id, live)
    try:
        result = live.agent.run_sync(
            deps=deps, message_history=live.messages, deferred_tool_results=results
        )
```

to:

```python
    deps = _make_deps(session, session_id, live)
    queued = live.drain_messages()
    user_prompt = "\n".join(queued) if queued else None
    try:
        result = live.agent.run_sync(
            user_prompt,
            deps=deps,
            message_history=live.messages,
            deferred_tool_results=results,
        )
```

(d) After `submit_human_command`, add `submit_message`:

```python
def submit_message(
    session: Session, registry: SessionRegistry, session_id: int, text: str
) -> None:
    """Queue a free-text operator chat message for the agent (FR-17 Slice 4).

    Recorded as a ``HUMAN_MESSAGE`` transcript event (so the chat shows it and it
    replays) and buffered on the live session; delivered to the agent as a
    first-class user turn on the next approve/reject resume
    (:func:`_resume_with_decision`) — the pure-queue model (the agent is never
    idle, so a message can only land at the next turn boundary). Distinct from the
    `!` command path (:func:`submit_human_command`): a chat message is the
    operator's *voice*, not an observed command result.

    A no-op if the session is not live (already ended/concluded, or never
    started) — there is nothing to steer once torn down.

    Args:
        session: Active DB session for this call.
        registry: The live-session registry (holds the message buffer).
        session_id: The retest session to message.
        text: The exact operator message.
    """
    live = registry.get(session_id)
    if live is None:
        return
    append_event(session, session_id, SessionEventKind.HUMAN_MESSAGE, {"text": text})
    live.receive_message(text)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_retest_session.py -v`
Expected: PASS (new message tests + all existing session tests — the `user_prompt=None` path must not regress approve/reject/budget/plan tests).

- [ ] **Step 7: Lint + typecheck + complexity**

Run: `make lint && make typecheck && uv run xenon --max-absolute C --max-modules B --max-average A src`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/revalid/domain.py src/revalid/retest_session.py tests/_retest_helpers.py tests/unit/test_retest_session.py
git commit -m "feat(retest): queue operator chat messages, deliver as user turn on next decision (FR-17 Slice 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: REST endpoint `POST /message`

**Files:**
- Modify: `src/revalid/app.py`
- Test: `tests/integration/test_retest_session_api.py`

**Interfaces:**
- Consumes: Task 2's `submit_message`; existing `run_human_command` pattern, `_register_session_routes(router, sessions, registry)`, `BackgroundTasks`.
- Produces: `MessageRequest{text: str}`; `run_message(sessions, registry, session_id, text)`; `POST /api/retest-sessions/{session_id}/message` → `202 {"status": "accepted"}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/integration/test_retest_session_api.py` (extend its `tests._retest_helpers` import with `script_run_then_conclude_noting_message`):

```python
def test_retest_session_message_recorded_and_buffered() -> None:
    """A chat message POSTed to a live session lands in the transcript."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        resp = client.post(f"/api/retest-sessions/{sid}/message", json={"text": "focus on login"})
        assert resp.status_code == 202

        state = client.get(f"/api/retest-sessions/{sid}").json()
        msg = next(e for e in state["events"] if e["kind"] == "human_message")
        assert msg["payload"]["text"] == "focus on login"


def test_retest_session_message_rejects_empty() -> None:
    """An empty message is a 422 (the request model requires non-empty text)."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        resp = client.post(f"/api/retest-sessions/{sid}/message", json={"text": ""})
        assert resp.status_code == 422


def test_retest_session_message_delivered_on_next_decision() -> None:
    """A queued message reaches the agent on the next approval (over HTTP)."""
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_retest_agent] = lambda: build_retest_agent(
        FunctionModel(script_run_then_conclude_noting_message)
    )
    box = FakeSandbox(
        lambda cmd: CommandResult(stdout=f"out:{cmd}", stderr="", exit_code=0, elapsed_ms=1)
    )
    app.dependency_overrides[get_sandbox_factory] = lambda: lambda _sid: box
    with TestClient(app) as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        state = client.get(f"/api/retest-sessions/{sid}").json()
        cid = next(
            e for e in state["events"] if e["kind"] == "command_proposed"
        )["payload"]["tool_call_id"]

        client.post(f"/api/retest-sessions/{sid}/message", json={"text": "focus on login"})
        client.post(f"/api/retest-sessions/{sid}/commands/{cid}/approve")

        final = client.get(f"/api/retest-sessions/{sid}").json()
        assert final["verdict_rationale"] == "saw-message"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -m integration tests/integration/test_retest_session_api.py -k message -v`
Expected: FAIL — the `/message` route is 404 / `MessageRequest` undefined.

- [ ] **Step 3: Implement in `src/revalid/app.py`.**

(a) Add `submit_message` to the `revalid.retest_session` import block (line ~91–100), keeping it sorted.

(b) After `HumanCommandRequest` (line ~318) add:

```python
class MessageRequest(BaseModel):
    """Body for an operator chat message to the agent (FR-17 Slice 4)."""

    text: str = Field(min_length=1)
```

(c) After `run_human_command` (line ~649) add:

```python
def run_message(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    text: str,
) -> None:
    """Queue an operator chat message for the agent (FR-17 Slice 4 background task).

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The retest session to message.
        text: The exact operator message.
    """
    with sessions() as session:
        submit_message(session, registry, session_id, text)
```

(d) In `_register_session_routes`, after the `human_command` route and before `end_retest_session`, add:

```python
    @router.post("/retest-sessions/{session_id}/message", status_code=202)
    def send_message(
        session_id: int, body: MessageRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Queue an operator chat message to the agent (FR-17 Slice 4).

        Recorded on the transcript and delivered to the agent as a user turn on
        its next approve/reject (pure-queue steering). A no-op if the session is
        no longer live.
        """
        background.add_task(run_message, sessions, registry, session_id, body.text)
        return {"status": "accepted"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -m integration tests/integration/test_retest_session_api.py -v`
Expected: PASS.

- [ ] **Step 5: Complexity gate (may require a split)**

Run: `uv run xenon --max-absolute C --max-modules B --max-average A src`
Expected: clean. **If `_register_session_routes` trips `--max-absolute C`** (it now holds seven nested routes), extract the new route exactly like `_register_session_stream_route`: create

```python
def _register_session_message_route(
    router: APIRouter, sessions: sessionmaker[Session], registry: SessionRegistry
) -> None:
    """Register the FR-17 Slice 4 chat-message route (split out for the mccabe gate)."""

    @router.post("/retest-sessions/{session_id}/message", status_code=202)
    def send_message(
        session_id: int, body: MessageRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Queue an operator chat message to the agent (FR-17 Slice 4)."""
        background.add_task(run_message, sessions, registry, session_id, body.text)
        return {"status": "accepted"}
```

remove `send_message` from `_register_session_routes`, and call `_register_session_message_route(router, sessions, registry)` right where `_register_session_stream_route(...)` is called in `create_app`. Re-run the gate + integration tests.

- [ ] **Step 6: Lint + typecheck**

Run: `make lint && make typecheck`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(retest): POST /message endpoint for operator chat steering (FR-17 Slice 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: frontend — chat input, operator turn, queue UX

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/routes/RetestSession.tsx`
- Test: `frontend/src/routes/RetestSession.test.tsx`

**Interfaces:**
- Consumes: Task 3's `POST /message`; existing `jsonInit`, `request`, `useRetestSession`, `SessionEvent`, `AgentTurn`.
- Produces: `submitMessage(id: number, text: string): Promise<{ status: string }>`; a `HumanTurn` chat item for `human_message` events; plain-text input path posts a message (Send) while `!` still runs a command (Run).

- [ ] **Step 1: Add the API client + its failing test.** In `frontend/src/api/client.ts`, after `submitHumanCommand`:

```typescript
/**
 * Send a free-text chat message to the retest agent (FR-17 Slice 4). Queued
 * server-side and delivered to the agent as a user turn on its next
 * approve/reject (pure-queue steering).
 */
export function submitMessage(id: number, text: string): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/message`,
    jsonInit("POST", { text }),
  );
}
```

- [ ] **Step 2: Write the failing frontend tests.** In `frontend/src/routes/RetestSession.test.tsx`: **replace** the existing `it("treats non-! text as chat (hinted), not a command", ...)` block with the three tests below (add nothing to the import — `vi.mock("../api/client")` auto-mocks `submitMessage`):

```tsx
  it("sends non-! text to the agent as a chat message", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.submitMessage).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    await userEvent.type(screen.getByLabelText(/operator console input/i), "focus on login");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(client.submitMessage).toHaveBeenCalledWith(1, "focus on login");
    expect(client.submitHumanCommand).not.toHaveBeenCalled();
  });

  it("renders a human_message as an operator turn", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "human_message", payload: { text: "focus on login" } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText("focus on login")).toBeInTheDocument();
  });

  it("disables the input once the session is over", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "concluded",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByLabelText(/operator console input/i)).toBeDisabled();
  });
```

- [ ] **Step 3: Run to verify they fail**

Run: `npm --prefix frontend run test -- RetestSession`
Expected: FAIL — `submitMessage` not called (Send button absent), human turn not rendered.

- [ ] **Step 4: Implement in `frontend/src/routes/RetestSession.tsx`.**

(a) Extend the import from `../api/client` to include `submitMessage`.

(b) After the `humanCommandMutation`, add:

```tsx
  // Plain-text chat to the agent (FR-17 Slice 4); queued server-side and read on
  // the agent's next turn. Separate mutation so its state is independent.
  const messageMutation = useMutation({
    mutationFn: (text: string) => submitMessage(id, text),
  });
```

(c) Add a `HumanTurn` component + a queued-detection helper near `AgentTurn`:

```tsx
/** One operator chat message: a right-aligned, iris-tinted bubble in the center chat. */
function HumanTurn({ text, queued }: { text: string; queued: boolean }) {
  return (
    <div className="flex justify-end">
      <div className="min-w-0 max-w-[85%] rounded-lg border border-iris/40 bg-iris/10 px-4 py-3">
        <p className="whitespace-pre-wrap text-sm text-fg">{text}</p>
        {queued && (
          <p className="mt-1 text-[11px] text-faint">queued — sent on your next approve/reject</p>
        )}
      </div>
    </div>
  );
}

/** Seq of the latest approve/reject; a human_message after it hasn't been delivered yet. */
function lastDecisionSeq(events: SessionEvent[]): number {
  const decisions = new Set([
    "command_approved",
    "command_rejected",
    "plan_approved",
    "plan_rejected",
  ]);
  const latest = [...events].reverse().find((event) => decisions.has(event.kind));
  return latest ? latest.seq : 0;
}
```

(d) Replace the input-derivation block:

```tsx
  const trimmed = input.trim();
  const isCommand = trimmed.startsWith("!");
  const commandBody = isCommand ? trimmed.slice(1).trim() : "";
  const sessionOver = OVER_STATUSES.has(status);
  const canRun = commandBody.length > 0 && !sessionOver;
```

with:

```tsx
  const trimmed = input.trim();
  const isCommand = trimmed.startsWith("!");
  const commandBody = isCommand ? trimmed.slice(1).trim() : "";
  const sessionOver = OVER_STATUSES.has(status);
  // `!command` runs in the sandbox (Slice 2); plain text is a chat message to the
  // agent (Slice 4). Both need non-empty content and a live session.
  const hasContent = isCommand ? commandBody.length > 0 : trimmed.length > 0;
  const canSubmit = hasContent && !sessionOver;
```

(e) Add the decision-seq value near the other derived values (after `planSteps`):

```tsx
  const decisionSeq = lastDecisionSeq(events);
```

(f) In the `chatItems` flatMap, add a `human_message` branch (e.g. right after the `agent_message` branch):

```tsx
    if (event.kind === "human_message") {
      return [
        <HumanTurn
          key={event.seq}
          text={String(event.payload.text ?? "")}
          queued={event.seq > decisionSeq && !sessionOver}
        />,
      ];
    }
```

(g) Replace the `<form>` block (the operator console) with:

```tsx
      {/* Operator console: plain text messages the agent; `!<command>` runs it. */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!canSubmit) return;
          if (isCommand) humanCommandMutation.mutate(commandBody);
          else messageMutation.mutate(trimmed);
          setInput("");
        }}
        className="shrink-0"
      >
        <div className="flex items-center gap-2 rounded-lg border border-line bg-panel/60 px-3 py-2">
          <input
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
            }}
            placeholder="Message the agent — or !command to run it in the sandbox"
            disabled={sessionOver}
            aria-label="Operator console input"
            className="min-w-0 flex-1 bg-transparent font-mono text-[13px] text-fg outline-none placeholder:text-faint disabled:opacity-45"
          />
          <Button type="submit" variant="ghost" disabled={!canSubmit}>
            {isCommand ? "Run" : "Send"}
          </Button>
        </div>
        {!sessionOver && (
          <p className="mt-1 px-1 text-[11px] text-faint">
            {isCommand ? (
              <>Runs once in the egress-locked sandbox.</>
            ) : (
              <>Messages are read on the agent&apos;s next turn — approve or reject a pending step to deliver now.</>
            )}
          </p>
        )}
        {humanCommandMutation.isError && (
          <p role="alert" className="mt-1 px-1 text-sm text-danger-fg">
            {errorMessage(humanCommandMutation.error)}
          </p>
        )}
        {messageMutation.isError && (
          <p role="alert" className="mt-1 px-1 text-sm text-danger-fg">
            {errorMessage(messageMutation.error)}
          </p>
        )}
      </form>
```

- [ ] **Step 5: Run to verify they pass**

Run: `npm --prefix frontend run test -- RetestSession`
Expected: PASS (new tests + existing RetestSession tests — the `!`-command test still finds a `Run` button).

- [ ] **Step 6: Full frontend gate**

Run: `npm --prefix frontend run test && npm --prefix frontend run lint && npm --prefix frontend run build`
Expected: all green (vitest, eslint, tsc + vite build).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/RetestSession.tsx frontend/src/routes/RetestSession.test.tsx
git commit -m "feat(ui): chat input — steer & ask the retest agent, queued operator turns (FR-17 Slice 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: docs — ADR-0028, SRS acceptance criteria, roadmap

**Files:**
- Create: `docs/adr/0028-agentic-retest-chat-steering.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/requirements/srs.md`
- Modify: `docs/roadmap.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Create the ADR** `docs/adr/0028-agentic-retest-chat-steering.md` (MADR, matching 0025–0027 style):

```markdown
# ADR-0028: Agentic retest chat steering & Q&A (FR-17 Slice 4)

- Status: proposed
- Date: 2026-07-16
- Deciders: Álvaro Navarro
- Part of: FR-17 / M6, epic #87; builds on ADR-0025 (console), ADR-0026 (`!` commands), ADR-0027 (guiding plan).

## Context

FR-17's third steering channel is **chat**: the operator types free-text to steer the
agent ("focus on the login endpoint") or ask questions ("what did that 500 mean?"),
completing the trio with *approve/edit* and *type a command*.

The Slice 0 gate constrains delivery. The agent is never idle — it is either running,
suspended on a deferred tool call (the common resting state), or terminal — and Pydantic
AI cannot accept a new user prompt while a tool call is deferred, nor interrupt a running
turn. So an operator message can only be **buffered and delivered at the next turn
boundary**, which while commands are gated is the next approve/reject.

## Decision

1. **Pure-queue delivery, never an interrupt.** A message is buffered on the live session
   and delivered on the next approve/reject; it never silently discards a pending
   proposal. Redirect = reject-with-message; augment = approve-with-message. (The
   autonomous drain — picking messages up mid-loop without a gate — arrives with
   free-launch, Slice 5.)
2. **Delivered as a first-class user turn.** On the resume, the drained message is passed
   as `user_prompt` alongside `deferred_tool_results`, so it lands as a real
   `UserPromptPart` after the tool return — a model responds to a user turn far more
   reliably than to prose folded into a tool result.
3. **Q&A via a non-gated `respond` tool.** The agent emits prose through `respond`
   (`agent_message` event); being a normal tool, the run continues to its next
   proposal/verdict. No new output type, no new session state, budget-exempt.
4. **Observed-fact vs operator-voice split.** `!`-command *results* (Slice 2) stay folded
   into the tool result the agent reads; chat messages are the operator's *voice* (a user
   turn). Two channels, two framings.
5. **No dequeue (audit).** A sent message is committed to the append-only transcript
   immediately (evidence, NFR-02); the UI shows undelivered messages with a "queued"
   treatment but there is no edit/remove.

## Alternatives considered

- **Message implies reject** (auto-reject the pending proposal so a steer takes effect in
  one action) — rejected: silently discards a proposal and breaks augment-and-approve.
- **Fold the message into the tool-result string** (like `!` observations) — rejected: a
  model treats a buried tool-result note as data, not an instruction; a user turn is read
  reliably.
- **A new non-terminal prose `output_type`** — rejected: the run would end on prose,
  forcing the orchestrator to re-drive with no user prompt; a `respond` tool keeps prose a
  mid-run side-effect with no loop change.

## Consequences

- **+** Full three-channel steering; Q&A reuses the already-reserved `agent_message`
  plumbing; zero new session states; no change to the gate, budget, or egress lock.
- **−** A message sent while the agent finalizes a `conclude` may go unread (still
  recorded); a question asked while a command is pending is answered on resolving it —
  both inherent to the pure-queue gate, stated plainly.
```

- [ ] **Step 2: Add the ADR to the index** — in `docs/adr/README.md`, after the 0027 row:

```markdown
| [0028](0028-agentic-retest-chat-steering.md) | Agentic retest chat steering & Q&A: pure-queue messages delivered as a first-class user turn on the next decision; non-gated `respond` tool for prose | proposed | 2026-07-16 |
```

- [ ] **Step 3: Add Slice 4 acceptance criteria to the SRS** — in `docs/requirements/srs.md`, after the Slice 0 AC block of FR-17 (after line ~144), insert:

```markdown
- **Acceptance criteria — Slice 4** (met — issue #96, ADR-0028 proposed, 2026-07-16):
  - [x] **AC5**: the operator can type a free-text message into the console; it is recorded as a `human_message` transcript event and queued on the live session (a no-op if the session is not live).
  - [x] **AC6**: a queued message is delivered to the agent as a first-class user turn (`user_prompt`) on the next approve/reject, in order — never interrupting a run nor discarding a pending proposal (pure-queue steering).
  - [x] **AC7**: the agent can answer in prose via a non-gated `respond` tool (an `agent_message` event) and the run continues to its next proposal/verdict; messages and `respond` consume no step budget.
  - [x] **AC8**: the SPA sends plain text as a chat message (Send) while `!command` still runs (Run); operator messages render as a distinct turn with a "queued" treatment until delivered; the input disables when the session is over.
```

Then update the FR-17 **"Deferred to later slices"** line to the current numbering (the current line uses stale pre-reslice numbering):

```markdown
- **Deferred to later slices** (tracked in epic #87): free-launch mode + session controls + step/time budget + give-up UI (Slice 5); verdict adjudication UI + FR-09/FR-10/FR-12 integration and retirement of the old batch path (Slice 6).
```

(The SRS currently holds only AC1–AC4, from Slice 0; Slices 1–3 were tracked in the roadmap, not the SRS — so Slice 4's criteria are the next unused indices, AC5–AC8.)

- [ ] **Step 4: Update the roadmap** — in `docs/roadmap.md`, tick the M6 Slice 4 checkbox (the `- [ ] **Slice 4**` line) and add a dated state note under "Current state", mirroring the prior slice notes:

```markdown
**2026-07-16 (M6) — Slice 4 built (FR-17):** chat input / steering & Q&A (issue #96, branch `feat/fr17-chat-steering-slice4`, **ADR-0028** proposed). The operator types free-text into the console to steer the agent or ask it questions; messages **queue** and are delivered as a first-class `user_prompt` on the next approve/reject (pure-queue, forced by the deferred-tool gate — the agent is never idle), never interrupting a run or discarding a pending proposal. A non-gated **`respond`** tool lets the agent answer in prose (reusing the already-reserved `agent_message` event), budget-exempt. `!`-command results stay folded into the tool result (observed fact); chat is the operator's voice (a user turn). New: `SessionEventKind.HUMAN_MESSAGE`, `POST /api/retest-sessions/{id}/message`, a `human_messages` buffer on `LiveSession`; SPA sends plain text as a message (Send) with a Claude-Code-style "queued" treatment. No new session state; gate + egress lock untouched. `make demo-retest-session` unaffected.
```

- [ ] **Step 5: Verify the docs build** (no broken links / MkDocs nav)

Run: `uv run mkdocs build --strict 2>&1 | tail -5` (or `make docs` if that is the project target)
Expected: builds clean. If the ADR must be added to `mkdocs.yml` nav, add it alongside 0027.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0028-agentic-retest-chat-steering.md docs/adr/README.md docs/requirements/srs.md docs/roadmap.md
git commit -m "docs(retest): ADR-0028 chat steering; SRS AC + roadmap for Slice 4 (FR-17, #96)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (before PR)

- [ ] **Full backend suite:** `make test-unit && make test-integration` — green, `src/` coverage ≥ 80%.
- [ ] **Backend gates:** `make lint && make typecheck && uv run xenon --max-absolute C --max-modules B --max-average A src` — clean.
- [ ] **Frontend gates:** `npm --prefix frontend run test && npm --prefix frontend run lint && npm --prefix frontend run build` — green.
- [ ] **Behavioural verify** (invoke the `verify` skill): `make run`, start a session, type a message while a command is pending → it renders as a queued operator turn; approve → the agent reads it (its next turn reflects the steer / it answers via a chat turn); ask a question → the agent answers in prose. Confirm `!command` still runs.
- [ ] **Open the PR** with `Closes #96`, a "How to validate" section (the commands above + the behavioural walkthrough), and the ADR-0028 link. Queue auto-merge once required CI is green.

## Spec coverage check

- Spec §2/§3 (pure-queue, first-class `user_prompt`, observed-fact/operator-voice split) → Task 2 (delivery) + Task 3 (endpoint).
- Spec §4 (`respond` Q&A tool) → Task 1.
- Spec §5 (budget-exempt, not-live no-op, gate untouched) → Task 1/2 (`respond` non-gated; `submit_message` no-op) + covered by existing budget tests staying green.
- Spec §6 (`HUMAN_MESSAGE`, `POST /message`) → Task 2 (enum) + Task 3 (endpoint).
- Spec §7 (frontend input, operator turn, queue UX, no dequeue) → Task 4.
- Spec §9 (tests) → Tasks 1–4 tests.
- Spec §10 (ADR-0028, SRS AC, roadmap) → Task 5.
- Spec §11 limitations → recorded in ADR-0028 consequences (Task 5); no code needed.
```
