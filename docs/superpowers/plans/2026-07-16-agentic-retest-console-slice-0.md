# Agentic Retest Console — Slice 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a finding, the operator starts a sandboxed retest session; a Pydantic-AI agent proposes one shell command + rationale (gated); the operator approves it; it runs in an egress-locked Docker container; the agent sees the output (streamed to a read-only web terminal) and concludes with a verdict — **one approve → one exec → verdict**.

**Architecture:** A per-session ephemeral Docker sandbox on an `--internal` (egress-locked) network runs commands the agent proposes. The agent uses **Pydantic AI deferred tools**: `run_command` is `requires_approval=True`, so `agent.run_sync(...)` *returns* a `DeferredToolRequests` when the model proposes a command; the orchestrator persists it as a transcript event, the human approves/rejects over REST, and a fresh background step *resumes* the run with `ToolApproved`/`ToolDenied`. No suspended threads. The terminal conclusion is a `ConcludeOutput` structured output. An append-only `session_events` table is the durable transcript; a WebSocket tails it to the SPA (read-only `xterm.js` terminal + approval card + verdict banner).

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 (typed mapping) / Pydantic AI (`pydantic-ai-slim[anthropic,openai]>=2.9.0`) / Docker SDK (`docker>=7.0`, optional `sandbox` extra) / React 19 + Vite + TS + Tailwind + `@xterm/xterm` / pytest + vitest.

---

## Key implementation decisions (Álvaro's call — flagged for review)

These four are implementation-level choices the spec left open or delegated to this plan. Each is precedent-backed and reversible; **veto any before we start coding** and I'll revise the affected task(s).

1. **Gating = Pydantic AI deferred tools** (resolves spec §12 open-Q1). `run_command` is `requires_approval=True`; `output_type=[ConcludeOutput, DeferredToolRequests]`. Each agent step ends by either proposing a command (returns `DeferredToolRequests`, run pauses) or concluding (`ConcludeOutput`, run ends). Approval resumes with `agent.run_sync(message_history=..., deferred_tool_results=...)`. *Alternative rejected:* a background thread that blocks on a `threading.Event` — more code, blocked threads, no framework support.
   - **Refinement to flag:** the spec named two tools (`run_command` + `conclude`). We implement **one gated tool `run_command` + a `ConcludeOutput` structured output** (concluding = emitting the final structured output). Cleaner and avoids a redundant terminal tool.

2. **Docker access = `docker` Python SDK as an optional `sandbox` extra**, mirroring the `playwright`/`browser` precedent (`browser.py`): lazy-imported, live methods `# pragma: no cover`, `SandboxUnavailableError` → HTTP 501 when the extra is absent. *Alternative rejected:* `subprocess` to the `docker` CLI (repo shells out to `docker compose` elsewhere) — stringly-typed, draws ruff `S603/S607`, and there is no `subprocess` in `src/` today.

3. **WebSocket source = DB-poll tail** of the append-only `session_events` table (no in-process pub/sub broker). The WS *interface* (the forward-compatible part Slice 1's PTY reuses) is preserved; the *source* is the transcript table, polled every ~250 ms. Adequate because Slice 0 commands run to completion before an output event is written (true char-streaming is the Slice 1 shared-PTY concern). *Alternative rejected:* an async broker fed from sync background threads — needs `loop.call_soon_threadsafe` bridging, fragile.

4. **Live agent state = in-memory `SessionRegistry`** (message history + sandbox handle + budget counters), one registry per app instance. The `session_events` table is the durable transcript/audit; the registry is live-only. **A process restart abandons in-flight sessions** — acceptable for Slice 0 (sessions are ephemeral by design; the transcript survives). *Alternative deferred to a later slice:* serialize message history to the DB for restart-safe resumption.

---

## Global Constraints

Every task's requirements implicitly include these (values copied from `CLAUDE.md` + `pyproject.toml`):

- **Python 3.12+**, managed with `uv`; run tools via `uv run` / `make`.
- **`mypy --strict` must pass** (`files = ["src", "tests"]`). New optional-dep imports need a `[[tool.mypy.overrides]] ignore_missing_imports = true` block.
- **Ruff**: line length **100**; lint select `E,W,F,I,B,UP,S,C90,N,D,RUF`; **Google docstrings required on public API** (`D`), **bandit `S` on** for `src/`. Tests get `per-file-ignores = ["S101","S105","S106","D"]`.
- **Complexity gate (blocking in CI):** `xenon --max-absolute C --max-modules B --max-average A src`; ruff `C90 max-complexity = 10`. If a function trips it, **refactor, don't suppress**.
- **Coverage:** global floor **80 %** on `src/revalid` (unit job only, `branch = true`). New pure/logic modules aim for **100 % of non-live lines** — isolate live Docker lines behind lazy import + `# pragma: no cover - <reason>` (the `browser.py` precedent). Coverage auto-excludes only `if TYPE_CHECKING:` and `raise NotImplementedError`.
- **Tests by pyramid level:** `tests/unit/` (no I/O; LLM via `FunctionModel`), `tests/integration/` (`pytestmark = pytest.mark.integration`; real REST/WS surface, `FakeSandbox` + `FunctionModel`), `tests/system/` (`@pytest.mark.system`; real Docker + lab, nightly). `--strict-markers` — only `integration`/`system` are registered.
- **Conventional Commits** (commit-msg hook): `feat:`/`fix:`/`docs:`/`test:`/`refactor:`/`chore:`/`ci:`. Every commit carries `Co-Authored-By: Claude`.
- **Frontend gates:** eslint + `tsc` + `vite build` + vitest; global coverage floors + per-file 100 % pins on owned modules (`lib/status.ts`, `ui/Button`, `ui/Badge`, `PipelineTrack`, …). **Reuse** `ui/Button`, `ui/Badge`, `lib/status.ts` — don't re-roll them. **NFR-03: no CDN** — bundle `@xterm/xterm` + its CSS locally (like `@fontsource/*`).
- **App binds `127.0.0.1` only** (NFR-03, ADR-0008 single-user threat model).

## Before you start — issue-first (non-negotiable)

Open the Slice 0 issue on the board **before** writing code: `req:FR-17` label + **M6** milestone, as a child of epic **#87**. Use the `feature-request` skill or:

```bash
gh issue create --label "req:FR-17" --milestone "M6" \
  --title "FR-17 Slice 0: agentic retest console — sandbox + gated exec + live terminal + verdict" \
  --body "Implements Slice 0 of docs/superpowers/plans/2026-07-16-agentic-retest-console-slice-0.md. Parent epic #87."
```

Branch from the issue (e.g. `feat/fr17-retest-console-slice0`). The PR body MUST contain `Closes #<n>`.

---

## File Structure

**New backend modules** (each one job, narrow interface):

- `src/revalid/sandbox.py` — `CommandResult` (frozen), `Sandbox` Protocol, `FakeSandbox` (scripted, unit-tested), `DockerSandbox` (live, `# pragma: no cover`), `SandboxUnavailableError`, egress-network constants + pure helpers (`internal_network_name`, `egress_probe_command`). Blueprint: `src/revalid/browser.py`.
- `src/revalid/retest_agent.py` — `ConcludeOutput` (frozen), `RetestSessionDeps` (dataclass), `build_retest_agent(model=None)` with the gated `run_command` tool. Blueprint: `build_plan_agent` (`plan.py:118`).
- `src/revalid/retest_session.py` — the orchestrator: `RetestSessionStatus` transitions, `LiveSession`/`SessionRegistry`, event append (`append_event` with monotonic `seq`), `create_session`, `start_and_step`, `apply_decision`, `end_session`, budget → give-up, plus read helpers (`load_events_after`). Pure logic + DB; no HTTP.

**Modified backend files:**

- `src/revalid/domain.py` — add `RetestSessionStatus` + `SessionEventKind` `StrEnum`s next to `PlanStatus`/`ReportStatus`. Reuse existing `VerdictStatus` for the verdict.
- `src/revalid/db.py` — add `RetestSessionRecord` + `SessionEventRecord`.
- `src/revalid/app.py` — `get_retest_agent`/`RetestAgentDep`, `get_sandbox_factory`/`SandboxFactoryDep`, `_register_session_routes(api, sessions, registry)`, the WS endpoint, background workers `run_first_step`/`run_decision`, and `RetestSessionOut`/`SessionEventOut` response models. Build the `SessionRegistry` in `create_app`.
- `pyproject.toml` — `sandbox = ["docker>=7.0"]` optional extra + `docker.*` mypy override.

**New frontend files:**

- `frontend/src/hooks/useRetestSession.ts` — WS event stream (injectable socket) + `getRetestSession` GET fallback.
- `frontend/src/routes/RetestSession.tsx` (+ `RetestSession.test.tsx`) — read-only `xterm` terminal, approval card, verdict banner, End button.
- `frontend/src/components/RetestTerminal.tsx` (+ test) — the `xterm` wrapper (isolated so the route stays testable without a real terminal).

**Modified frontend files:** `frontend/src/api/client.ts` (session calls + WS URL helper), `frontend/src/App.tsx` (route), `frontend/src/routes/stages/RetestStage.tsx` (a "Start agentic retest session" entry point), `frontend/package.json` (`@xterm/xterm`), `frontend/vite.config.ts` (dev proxy `ws: true`).

**New tests / infra / docs:** `tests/unit/test_sandbox.py`, `tests/unit/test_retest_agent.py`, `tests/unit/test_retest_session.py`, `tests/integration/test_retest_session_api.py`, `tests/integration/test_retest_session_ws.py`, `tests/system/test_retest_session_system.py`; `lab/docker-compose.yml` (egress-locked `--internal` network); `.github/workflows/system-tests.yml` (`--extra sandbox`); `scripts/demo/retest_session.py` + `Makefile` `demo-retest-session`; `docs/adr/0025-*.md`, `docs/requirements/srs.md` (FR-17), `docs/roadmap.md` (M6 note).

---

## Task 1: Domain enums + verdict output type

**Files:**
- Modify: `src/revalid/domain.py` (add two `StrEnum`s near `PlanStatus`)
- Test: `tests/unit/test_domain.py` (append)

**Interfaces:**
- Produces: `RetestSessionStatus` (`STARTING="starting"`, `AWAITING_COMMAND="awaiting_command"`, `RUNNING_COMMAND="running_command"`, `CONCLUDED="concluded"`, `GIVEN_UP="given_up"`, `ENDED="ended"`, `ERROR="error"`); `SessionEventKind` (`AGENT_MESSAGE`, `COMMAND_PROPOSED`, `COMMAND_APPROVED`, `COMMAND_REJECTED`, `COMMAND_OUTPUT`, `STATE_CHANGE`, `VERDICT`, `ERROR`). Both consumed by Tasks 2–7.
- Reuse: existing `VerdictStatus` (`still_open`/`fixed`/`inconclusive`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_domain.py (append)
from revalid.domain import RetestSessionStatus, SessionEventKind

def test_retest_session_status_terminal_set() -> None:
    assert RetestSessionStatus.STARTING.value == "starting"
    terminal = {RetestSessionStatus.CONCLUDED, RetestSessionStatus.GIVEN_UP,
                RetestSessionStatus.ENDED, RetestSessionStatus.ERROR}
    assert RetestSessionStatus.AWAITING_COMMAND not in terminal

def test_session_event_kind_values() -> None:
    assert SessionEventKind.COMMAND_PROPOSED.value == "command_proposed"
    assert {k.value for k in SessionEventKind} >= {
        "command_proposed", "command_output", "verdict", "state_change"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_domain.py -k "retest_session_status or session_event_kind" -v`
Expected: FAIL — `ImportError: cannot import name 'RetestSessionStatus'`.

- [ ] **Step 3: Add the enums** (mirror `PlanStatus`, `domain.py:152-168`; include a docstring guaranteeing a terminal state so the SPA poll terminates)

```python
# src/revalid/domain.py (near PlanStatus / ReportStatus)
class RetestSessionStatus(StrEnum):
    """Lifecycle of an FR-17 agentic retest session.

    A session always reaches a terminal state (``CONCLUDED``/``GIVEN_UP``/
    ``ENDED``/``ERROR``) so the SPA poll and the WS tail terminate.
    """

    STARTING = "starting"
    AWAITING_COMMAND = "awaiting_command"
    RUNNING_COMMAND = "running_command"
    CONCLUDED = "concluded"
    GIVEN_UP = "given_up"
    ENDED = "ended"
    ERROR = "error"


class SessionEventKind(StrEnum):
    """Kinds of append-only transcript event (FR-17 audit trail)."""

    AGENT_MESSAGE = "agent_message"
    COMMAND_PROPOSED = "command_proposed"
    COMMAND_APPROVED = "command_approved"
    COMMAND_REJECTED = "command_rejected"
    COMMAND_OUTPUT = "command_output"
    STATE_CHANGE = "state_change"
    VERDICT = "verdict"
    ERROR = "error"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_domain.py -k "retest_session_status or session_event_kind" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/domain.py tests/unit/test_domain.py
git commit -m "feat(retest): session status + event-kind enums (FR-17)"
```

---

## Task 2: Sandbox — `CommandResult`, `Sandbox` protocol, `FakeSandbox`, live-excluded `DockerSandbox`

**Files:**
- Create: `src/revalid/sandbox.py`
- Modify: `pyproject.toml` (`sandbox` extra + mypy override)
- Test: `tests/unit/test_sandbox.py`

**Interfaces:**
- Produces:
  - `CommandResult` (frozen Pydantic model): `stdout: str`, `stderr: str`, `exit_code: int`, `elapsed_ms: int`.
  - `class Sandbox(Protocol)`: `start() -> None`, `exec(command: str, *, timeout: float) -> CommandResult`, `stop() -> None`.
  - `FakeSandbox(results: list[CommandResult] | Callable[[str], CommandResult])` — scripted; records `commands: list[str]`.
  - `SandboxUnavailableError(Exception)`.
  - `DockerSandbox(image: str, lab_container: str)` — live; `# pragma: no cover`.
  - Pure helpers: `internal_network_name(session_id: int) -> str`, `egress_probe_command(host: str) -> str`.
  - `SandboxFactory = Callable[[], Sandbox]`.
- Consumes: nothing project-internal.

- [ ] **Step 1: Write failing tests for the pure surface (`FakeSandbox`, helpers, error)**

```python
# tests/unit/test_sandbox.py
import pytest

from revalid.sandbox import (
    CommandResult,
    FakeSandbox,
    SandboxUnavailableError,
    egress_probe_command,
    internal_network_name,
)


def test_fake_sandbox_replays_scripted_results_and_records_commands() -> None:
    box = FakeSandbox([CommandResult(stdout="hi", stderr="", exit_code=0, elapsed_ms=3)])
    box.start()
    result = box.exec("echo hi", timeout=5.0)
    assert result.stdout == "hi"
    assert box.commands == ["echo hi"]
    box.stop()


def test_fake_sandbox_callable_script() -> None:
    box = FakeSandbox(lambda cmd: CommandResult(stdout=cmd, stderr="", exit_code=0, elapsed_ms=1))
    assert box.exec("whoami", timeout=1.0).stdout == "whoami"


def test_fake_sandbox_exhausted_raises() -> None:
    box = FakeSandbox([])
    with pytest.raises(SandboxUnavailableError):
        box.exec("echo hi", timeout=1.0)


def test_internal_network_name_is_session_scoped() -> None:
    assert internal_network_name(7) == "revalid-retest-7"


def test_egress_probe_command_targets_a_host() -> None:
    cmd = egress_probe_command("example.com")
    assert "example.com" in cmd
    assert "curl" in cmd
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'revalid.sandbox'`.

- [ ] **Step 3: Implement `sandbox.py`** — pure surface first, live Docker isolated behind lazy import + `# pragma: no cover` (copy the `browser.py` split: `browser.py:31` TYPE_CHECKING pragma, `browser.py:186/202` live pragmas + lazy import + `BrowserProbeUnavailableError` at `browser.py:49`)

```python
"""FR-17 / M6 egress-locked retest sandbox (ADR-0025, Slice 0).

An ephemeral Docker container on an ``--internal`` network (no host/internet
route) in which the retest agent runs one approved command at a time. The pure
surface (``CommandResult``, ``FakeSandbox``, helpers) is unit-tested; the live
``DockerSandbox`` needs the optional ``sandbox`` extra and is covered only by
the nightly system test — mirroring ``browser.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker.models.containers import Container

#: Pinned sandbox image (pentest CLIs: curl, etc.). Kept minimal for Slice 0.
DEFAULT_SANDBOX_IMAGE = "curlimages/curl:8.11.1"
#: The lab container name to attach to the internal network (lab/docker-compose.yml).
DEFAULT_LAB_CONTAINER = "revalid-juice-shop"


class CommandResult(BaseModel):
    """The captured result of one command run in the sandbox."""

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int


class Sandbox(Protocol):
    """One ephemeral, egress-locked execution environment for a retest session."""

    def start(self) -> None:
        """Provision the environment (idempotent)."""

    def exec(self, command: str, *, timeout: float) -> CommandResult:
        """Run ``command`` and capture its result."""

    def stop(self) -> None:
        """Tear the environment down; nothing persists."""


SandboxFactory = Callable[[], Sandbox]


class SandboxUnavailableError(Exception):
    """Raised when a sandbox is required but the runtime cannot provide one."""


def internal_network_name(session_id: int) -> str:
    """Return the per-session egress-locked Docker network name."""
    return f"revalid-retest-{session_id}"


def egress_probe_command(host: str) -> str:
    """Return a command that fails iff ``host`` is unreachable (egress-lock test)."""
    return f"curl --max-time 5 --silent --show-error --output /dev/null https://{host}"


class FakeSandbox:
    """A scripted in-memory sandbox for unit/integration tests (no Docker)."""

    def __init__(self, script: list[CommandResult] | Callable[[str], CommandResult]) -> None:
        self._script = script
        self.commands: list[str] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        """Mark the fake as started."""
        self.started = True

    def exec(self, command: str, *, timeout: float) -> CommandResult:  # noqa: ARG002
        """Return the next scripted result (or apply the callable)."""
        self.commands.append(command)
        if callable(self._script):
            return self._script(command)
        if not self._script:
            raise SandboxUnavailableError("FakeSandbox script exhausted")
        return self._script.pop(0)

    def stop(self) -> None:
        """Mark the fake as stopped."""
        self.stopped = True


class DockerSandbox:  # pragma: no cover - drives a live Docker daemon; covered by the system test
    """A real ephemeral Docker sandbox on an egress-locked ``--internal`` network."""

    def __init__(
        self,
        session_id: int,
        *,
        image: str = DEFAULT_SANDBOX_IMAGE,
        lab_container: str = DEFAULT_LAB_CONTAINER,
    ) -> None:
        self._session_id = session_id
        self._image = image
        self._lab_container = lab_container
        self._container: Container | None = None
        self._network_name = internal_network_name(session_id)

    def start(self) -> None:
        try:
            import docker
        except ImportError as exc:
            raise SandboxUnavailableError(
                "the sandbox extra is required: `uv sync --extra sandbox`"
            ) from exc
        client = docker.from_env()
        network = client.networks.create(self._network_name, driver="bridge", internal=True)
        network.connect(self._lab_container)  # allowlist == network membership (FR-06)
        self._container = client.containers.run(
            self._image, command="sleep infinity", network=self._network_name,
            detach=True, auto_remove=False, network_disabled=False,
        )

    def exec(self, command: str, *, timeout: float) -> CommandResult:
        import time
        if self._container is None:
            raise SandboxUnavailableError("sandbox not started")
        start = time.monotonic()
        code, output = self._container.exec_run(["sh", "-c", command], demux=True)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        stdout, stderr = output
        return CommandResult(
            stdout=(stdout or b"").decode(errors="replace"),
            stderr=(stderr or b"").decode(errors="replace"),
            exit_code=code, elapsed_ms=elapsed_ms,
        )

    def stop(self) -> None:
        import docker
        if self._container is not None:
            self._container.remove(force=True)
            self._container = None
        client = docker.from_env()
        try:
            network = client.networks.get(self._network_name)
            network.disconnect(self._lab_container, force=True)
            network.remove()
        except docker.errors.NotFound:
            pass
```

> Note: the `import time` / `import docker` inside methods keep the module importable without the extra and keep every live line under one `# pragma`. Verify `demux=True`'s `(stdout, stderr)` shape against the installed `docker` SDK in Task 11's system run.

- [ ] **Step 4: Add the optional extra + mypy override**

```toml
# pyproject.toml, [project.optional-dependencies]
# FR-17 agentic retest sandbox (ADR-0025). Optional: HTTP/unit paths never
# import it. Enable with `uv sync --extra sandbox`.
sandbox = ["docker>=7.0"]
```

```toml
# pyproject.toml, add alongside the playwright override
[[tool.mypy.overrides]]
module = ["docker.*"]
ignore_missing_imports = true
```

- [ ] **Step 5: Run tests + gates**

Run: `uv run pytest tests/unit/test_sandbox.py -v && uv run mypy && uv run ruff check src/revalid/sandbox.py`
Expected: tests PASS; mypy clean; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/sandbox.py tests/unit/test_sandbox.py pyproject.toml
git commit -m "feat(retest): egress-locked sandbox with FakeSandbox + live DockerSandbox (FR-17)"
```

---

## Task 3: DB tables + transcript persistence (`retest_sessions`, `session_events`)

**Files:**
- Modify: `src/revalid/db.py` (two `Base` subclasses)
- Create: `src/revalid/retest_session.py` (persistence layer only — orchestration lands in Task 5)
- Test: `tests/unit/test_retest_session.py`

**Interfaces:**
- Produces (DB): `RetestSessionRecord` (`id`, `finding_id` FK `findings.id`, `status`, `model`, `verdict_status|None`, `verdict_rationale|None`, `created_at`, `ended_at|None`); `SessionEventRecord` (`id`, `session_id` FK `retest_sessions.id`, `seq`, `kind`, `payload: JSON`, `created_at`).
- Produces (`retest_session.py`): `create_session(session, *, finding_id, model) -> RetestSessionRecord`; `append_event(session, session_id, kind, payload) -> SessionEventRecord` (monotonic `seq`); `load_events_after(session, session_id, after_seq) -> list[dict]`; `set_status(session, session_id, status) -> None`; `record_verdict(session, session_id, status, rationale) -> None`.
- Consumes: `RetestSessionStatus`, `SessionEventKind`, `VerdictStatus` (Task 1).

- [ ] **Step 1: Write failing persistence tests**

```python
# tests/unit/test_retest_session.py
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import RetestSessionStatus, SessionEventKind, VerdictStatus
from revalid.findings import import_findings  # existing helper that seeds a finding identity
from revalid import retest_session as rs


def _seed_finding(session) -> int:
    # mirror how other unit tests seed a finding identity (see tests/unit/test_findings.py)
    from revalid.domain import Finding
    record = import_findings(session, [Finding(title="SQLi", description="login bypass")])[0]
    return record.id


def test_append_event_assigns_monotonic_seq() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "starting"})
        rs.append_event(session, s.id, SessionEventKind.COMMAND_PROPOSED, {"command": "id"})
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["kind"] == "command_proposed"
    assert events[1]["payload"]["command"] == "id"


def test_record_verdict_writes_row_fields() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.record_verdict(session, s.id, VerdictStatus.STILL_OPEN, "auth still bypassable")
        session.refresh(s)
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_status == "still_open"
    assert s.verdict_rationale == "auth still bypassable"
```

> Adjust `_seed_finding` to match the real finding-seeding helper used in existing unit tests (`grep` `tests/unit/test_findings.py` for the import idiom). The point is a valid `findings.id` for the FK.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_retest_session.py -v`
Expected: FAIL — `ImportError` / missing tables.

- [ ] **Step 3: Add the two records to `db.py`** (mirror `VerdictRecord` `db.py:215-262` for columns; `_next_version` `findings.py:24-28` for the `seq` idiom)

```python
# src/revalid/db.py
class RetestSessionRecord(Base):
    """An FR-17 agentic retest session (parent of its append-only transcript)."""

    __tablename__ = "retest_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    verdict_status: Mapped[str | None] = mapped_column(String(16), default=None)
    verdict_rationale: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class SessionEventRecord(Base):
    """One append-only transcript event for a retest session (FR-17 audit)."""

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("retest_sessions.id"))
    seq: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

- [ ] **Step 4: Add the persistence layer `retest_session.py`**

```python
"""FR-17 / M6 retest-session persistence + orchestration (ADR-0025, Slice 0).

Task 3 adds the persistence layer; Task 5 adds the agent-driving orchestration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.db import RetestSessionRecord, SessionEventRecord
from revalid.domain import RetestSessionStatus, SessionEventKind, VerdictStatus

_TERMINAL: frozenset[RetestSessionStatus] = frozenset(
    {
        RetestSessionStatus.CONCLUDED,
        RetestSessionStatus.GIVEN_UP,
        RetestSessionStatus.ENDED,
        RetestSessionStatus.ERROR,
    }
)


def create_session(session: Session, *, finding_id: int, model: str) -> RetestSessionRecord:
    """Insert a ``starting`` session row and return it."""
    record = RetestSessionRecord(
        finding_id=finding_id, status=RetestSessionStatus.STARTING.value, model=model
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _next_seq(session: Session, session_id: int) -> int:
    """Return the next monotonic transcript sequence number for a session."""
    seqs = session.scalars(
        select(SessionEventRecord.seq).where(SessionEventRecord.session_id == session_id)
    ).all()
    return (max(seqs) + 1) if seqs else 1


def append_event(
    session: Session, session_id: int, kind: SessionEventKind, payload: dict[str, Any]
) -> SessionEventRecord:
    """Append one transcript event with the next ``seq`` and commit."""
    event = SessionEventRecord(
        session_id=session_id, seq=_next_seq(session, session_id), kind=kind.value, payload=payload
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def load_events_after(session: Session, session_id: int, after_seq: int) -> list[dict[str, Any]]:
    """Return transcript events with ``seq > after_seq`` in order, as plain dicts."""
    rows = session.scalars(
        select(SessionEventRecord)
        .where(SessionEventRecord.session_id == session_id, SessionEventRecord.seq > after_seq)
        .order_by(SessionEventRecord.seq)
    ).all()
    return [{"seq": r.seq, "kind": r.kind, "payload": r.payload} for r in rows]


def set_status(session: Session, session_id: int, status: RetestSessionStatus) -> None:
    """Move a session to ``status`` and record a ``state_change`` transcript event."""
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    record.status = status.value
    session.commit()
    append_event(session, session_id, SessionEventKind.STATE_CHANGE, {"to": status.value})


def record_verdict(
    session: Session, session_id: int, status: VerdictStatus, rationale: str
) -> None:
    """Persist the agent verdict on the session row + a ``verdict`` transcript event."""
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return
    record.status = RetestSessionStatus.CONCLUDED.value
    record.verdict_status = status.value
    record.verdict_rationale = rationale
    record.ended_at = func.now()  # noqa: F821 - import func at top with the others
    session.commit()
    append_event(
        session, session_id, SessionEventKind.VERDICT,
        {"status": status.value, "rationale": rationale},
    )
```

> Fix the `func.now()` import (`from sqlalchemy import func`) — shown inline for clarity; add it to the import block. Every write commits, matching the single-writer background-task model (`findings._next_version` precedent).

- [ ] **Step 5: Run tests + gates**

Run: `uv run pytest tests/unit/test_retest_session.py -v && uv run mypy && uv run ruff check`
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/db.py src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "feat(retest): session + append-only transcript tables and persistence (FR-17)"
```

---

## Task 4: The retest agent — gated `run_command` + `ConcludeOutput` (validate the deferred-tool gate FIRST)

Spec §12 open-Q1: "the suspend-on-approval gate is validated first thing in Slice 0." This task's first test proves the full deferred-approve-resume cycle at the agent level before any REST/WS is built on it.

**Files:**
- Create: `src/revalid/retest_agent.py`
- Test: `tests/unit/test_retest_agent.py`

**Interfaces:**
- Produces: `ConcludeOutput` (frozen: `status: VerdictStatus`, `rationale: str = Field(min_length=1)`); `RetestSessionDeps` (dataclass: `sandbox: Sandbox`, `emit_output: Callable[[str, CommandResult], None]`, `command_timeout: float = 30.0`); `build_retest_agent(model=None) -> Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]`.
- Consumes: `Sandbox`/`CommandResult` (Task 2), `VerdictStatus` (Task 1), `resolve_model` (`llm.py:45`).

- [ ] **Step 1: Write the failing gate-cycle test** (stateful `FunctionModel`: first turn proposes `run_command`, second turn — after the tool result — concludes)

```python
# tests/unit/test_retest_agent.py
from typing import Any

from pydantic_ai import DeferredToolRequests, ToolApproved, ToolDenied
from pydantic_ai.messages import (
    ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.domain import VerdictStatus
from revalid.retest_agent import ConcludeOutput, RetestSessionDeps, build_retest_agent
from revalid.sandbox import CommandResult, FakeSandbox


def _has_command_result(messages: list[ModelMessage]) -> bool:
    """True once a run_command ToolReturnPart is present in history."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == "run_command"
        for m in messages if isinstance(m, ModelRequest)
        for part in m.parts
    )


def _script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if not _has_command_result(messages):
        return ModelResponse(parts=[ToolCallPart(
            tool_name="run_command",
            args={"command": "curl -s http://revalid-juice-shop:3000/rest/user/login",
                  "rationale": "retry the login-bypass payload"})])
    output_tool = info.output_tools[0].name  # ConcludeOutput's tool
    return ModelResponse(parts=[ToolCallPart(
        tool_name=output_tool,
        args={"status": "still_open", "rationale": "auth still bypassable"})])


def test_deferred_gate_cycle_runs_command_then_concludes() -> None:
    box = FakeSandbox([CommandResult(stdout="{token: ...}", stderr="", exit_code=0, elapsed_ms=12)])
    outputs: list[tuple[str, CommandResult]] = []
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda cmd, res: outputs.append((cmd, res)))
    agent = build_retest_agent(FunctionModel(_script))

    # Turn 1: proposes a command -> run pauses with a deferred approval request.
    first = agent.run_sync("Retest the SQLi finding.", deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    [call] = first.output.approvals
    assert call.tool_name == "run_command"
    assert "curl" in call.args["command"]
    assert box.commands == []  # NOT executed before approval

    # Approve -> resume: the tool executes in the sandbox, model concludes.
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = ToolApproved()
    second = agent.run_sync(deps=deps, message_history=first.all_messages(),
                            deferred_tool_results=results)
    assert isinstance(second.output, ConcludeOutput)
    assert second.output.status == VerdictStatus.STILL_OPEN
    assert box.commands == ["curl -s http://revalid-juice-shop:3000/rest/user/login"]
    assert outputs and outputs[0][1].stdout.startswith("{token")


def test_reject_returns_reason_to_the_model() -> None:
    box = FakeSandbox([])  # nothing should execute on a rejection
    deps = RetestSessionDeps(sandbox=box, emit_output=lambda *_: None)
    agent = build_retest_agent(FunctionModel(_script))
    first = agent.run_sync("Retest.", deps=deps)
    [call] = first.output.approvals
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = ToolDenied("out of scope host")
    # After denial the (scripted) model concludes; the point is no sandbox exec happened.
    agent.run_sync(deps=deps, message_history=first.all_messages(), deferred_tool_results=results)
    assert box.commands == []
```

> `DeferredToolResults` import: `from pydantic_ai import DeferredToolResults`. **First implementation step (below) verifies the exact symbols/signature against the installed `pydantic-ai` version** — the resume call may accept the continuation prompt positionally (`run_sync("Continue", ...)`) in some 2.x builds; adjust if the no-prompt form errors.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_retest_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'revalid.retest_agent'`.

- [ ] **Step 3: Verify the deferred-tool API against the installed version, then implement**

Quick REPL check before writing (de-risks the whole slice):
```bash
uv run python -c "from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied; print('ok')"
```
Then implement `retest_agent.py`:

```python
"""FR-17 / M6 agentic retest agent (ADR-0025, Slice 0).

One gated ``run_command`` tool (Pydantic AI deferred approval) + a
``ConcludeOutput`` structured verdict. The orchestrator (retest_session.py)
runs the agent step-by-step, pausing on each proposed command for human
approval and resuming with ``ToolApproved``/``ToolDenied``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import VerdictStatus
from revalid.llm import resolve_model
from revalid.sandbox import CommandResult, Sandbox

_MAX_TOOL_RETRIES = 2

_INSTRUCTIONS = """\
You are a penetration-test *retester*. You are given one finding to re-verify \
against an authorised lab target that is reachable from your sandbox.

Rules:
- Work one command at a time. Propose a single shell command plus a one-line \
rationale; a human approves or rejects each before it runs.
- The sandbox can reach ONLY the lab target — never the internet or the host.
- Prefer non-destructive verification. Do not attempt to damage the target.
- When you are confident, conclude with a verdict: `still_open` (the issue \
reproduces), `fixed` (it does not), or `inconclusive` (you cannot tell).
"""


class ConcludeOutput(BaseModel):
    """The agent's terminal verdict for a retest session."""

    model_config = ConfigDict(frozen=True)

    status: VerdictStatus
    rationale: str = Field(min_length=1)


@dataclass
class RetestSessionDeps:
    """Runtime dependencies injected into the retest agent's tools."""

    sandbox: Sandbox
    emit_output: Callable[[str, CommandResult], None]
    command_timeout: float = 30.0


def _format_result(result: CommandResult) -> str:
    """Render a command result as the tool-return text the model observes."""
    return (
        f"exit_code={result.exit_code} elapsed_ms={result.elapsed_ms}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def build_retest_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]:
    """Build the FR-17 retest agent: one gated ``run_command`` tool + a verdict output."""
    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests] = Agent(
        model if model is not None else resolve_model(),
        deps_type=RetestSessionDeps,
        output_type=[ConcludeOutput, DeferredToolRequests],
        instructions=_INSTRUCTIONS,
        retries=_MAX_TOOL_RETRIES,
        defer_model_check=True,
    )

    @agent.tool(requires_approval=True)
    def run_command(ctx: RunContext[RetestSessionDeps], command: str, rationale: str) -> str:
        """Run one shell command in the egress-locked sandbox and return its output.

        Args:
            ctx: The run context carrying the sandbox + output-emit callback.
            command: The exact shell command to execute (lab target only).
            rationale: A one-line reason this command advances the retest.

        Returns:
            The command's exit code, timing, stdout and stderr as text.
        """
        result = ctx.deps.sandbox.exec(command, timeout=ctx.deps.command_timeout)
        ctx.deps.emit_output(command, result)
        return _format_result(result)

    return agent
```

- [ ] **Step 4: Run tests + gates**

Run: `uv run pytest tests/unit/test_retest_agent.py -v && uv run mypy && uv run ruff check`
Expected: PASS / clean. If the resume call signature differs, fix both the test and any orchestrator assumption in Task 5.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/retest_agent.py tests/unit/test_retest_agent.py
git commit -m "feat(retest): deferred-tool retest agent (gated run_command + verdict) (FR-17)"
```

---

## Task 5: Orchestrator — registry, step driver, approve/reject, budget → give-up

**Files:**
- Modify: `src/revalid/retest_session.py` (append the orchestration layer)
- Test: `tests/unit/test_retest_session.py` (append)

**Interfaces:**
- Produces:
  - `@dataclass LiveSession`: `agent`, `sandbox`, `deps: RetestSessionDeps`, `messages: list[ModelMessage]`, `pending_call_id: str | None`, `step_count: int`, `max_steps: int`.
  - `class SessionRegistry`: `put(session_id, live)`, `get(session_id) -> LiveSession | None`, `drop(session_id)`.
  - `start_and_step(session, registry, session_id, agent, sandbox, finding_prompt, *, max_steps=8) -> None` — starts the sandbox, runs the first agent step, persists the outcome.
  - `apply_decision(session, registry, session_id, *, approved, reason="") -> None` — resumes the paused run.
  - `end_session(session, registry, session_id) -> None`.
- Consumes: Task 3 persistence, Task 4 agent, Task 2 sandbox.

- [ ] **Step 1: Write the failing orchestration tests** (drive a full cycle + the budget backstop with `FakeSandbox` + `FunctionModel`)

```python
# tests/unit/test_retest_session.py (append)
from pydantic_ai.models.function import FunctionModel
from revalid.retest_agent import build_retest_agent
from revalid.sandbox import CommandResult, FakeSandbox
from revalid.retest_session import SessionRegistry, apply_decision, start_and_step
# reuse _script / _seed_finding patterns from the agent + earlier tests


def test_full_cycle_proposes_runs_and_concludes() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
        agent = build_retest_agent(FunctionModel(_script))  # proposes run_command then concludes
        start_and_step(session, registry, s.id, agent, box, "Retest the SQLi finding.")
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
        assert "command_proposed" in kinds

        apply_decision(session, registry, s.id, approved=True)
        session.refresh(s)
        assert s.status == RetestSessionStatus.CONCLUDED.value
        assert s.verdict_status == "still_open"
        assert box.commands and box.stopped  # ran once, torn down


def test_budget_exhaustion_gives_up() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox(lambda cmd: CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1))
        agent = build_retest_agent(FunctionModel(_always_propose))  # never concludes
        start_and_step(session, registry, s.id, agent, box, "Retest.", max_steps=1)
        apply_decision(session, registry, s.id, approved=True)  # would exceed the 1-step budget
        session.refresh(s)
    assert s.status == RetestSessionStatus.GIVEN_UP.value
    assert s.verdict_status == "inconclusive"
```

> Add `_always_propose` (a `FunctionModel` that always returns a `run_command` `ToolCallPart`, never the output tool) next to `_script`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_retest_session.py -k "full_cycle or budget" -v`
Expected: FAIL — `ImportError: cannot import name 'start_and_step'`.

- [ ] **Step 3: Implement the orchestration layer** (append to `retest_session.py`; keep each function ≤ complexity C — split the `result.output` dispatch into a helper)

```python
# src/revalid/retest_session.py (append)
from dataclasses import dataclass, field

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.messages import ModelMessage

from revalid.retest_agent import ConcludeOutput, RetestSessionDeps
from revalid.sandbox import CommandResult, Sandbox


@dataclass
class LiveSession:
    """In-memory live state for one active session (not restart-safe, Slice 0)."""

    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]
    sandbox: Sandbox
    deps: RetestSessionDeps
    messages: list[ModelMessage] = field(default_factory=list)
    pending_call_id: str | None = None
    step_count: int = 0
    max_steps: int = 8


class SessionRegistry:
    """Process-local registry of live sessions (one per app instance)."""

    def __init__(self) -> None:
        self._live: dict[int, LiveSession] = {}

    def put(self, session_id: int, live: LiveSession) -> None:
        self._live[session_id] = live

    def get(self, session_id: int) -> LiveSession | None:
        return self._live.get(session_id)

    def drop(self, session_id: int) -> None:
        self._live.pop(session_id, None)


def _make_deps(session: Session, session_id: int, sandbox: Sandbox) -> RetestSessionDeps:
    """Build agent deps whose output callback appends a command_output event."""

    def emit(command: str, result: CommandResult) -> None:
        append_event(
            session, session_id, SessionEventKind.COMMAND_OUTPUT,
            {"command": command, "stdout": result.stdout, "stderr": result.stderr,
             "exit_code": result.exit_code, "elapsed_ms": result.elapsed_ms},
        )

    return RetestSessionDeps(sandbox=sandbox, emit_output=emit)


def _dispatch_output(session: Session, registry: SessionRegistry, session_id: int, result: Any) -> None:
    """Persist the outcome of one agent step and set the next status."""
    live = registry.get(session_id)
    if live is None:
        return
    live.messages = result.all_messages()
    output = result.output
    if isinstance(output, DeferredToolRequests) and output.approvals:
        call = output.approvals[0]
        live.pending_call_id = call.tool_call_id
        append_event(session, session_id, SessionEventKind.COMMAND_PROPOSED,
                     {"command": call.args["command"], "rationale": call.args["rationale"],
                      "tool_call_id": call.tool_call_id})
        set_status(session, session_id, RetestSessionStatus.AWAITING_COMMAND)
    elif isinstance(output, ConcludeOutput):
        record_verdict(session, session_id, output.status, output.rationale)
        _teardown(registry, session_id)


def _teardown(registry: SessionRegistry, session_id: int) -> None:
    """Stop the sandbox and drop the live session."""
    live = registry.get(session_id)
    if live is not None:
        live.sandbox.stop()
        registry.drop(session_id)


def start_and_step(
    session: Session, registry: SessionRegistry, session_id: int,
    agent: Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests],
    sandbox: Sandbox, finding_prompt: str, *, max_steps: int = 8,
) -> None:
    """Start the sandbox and run the first agent step."""
    sandbox.start()
    deps = _make_deps(session, session_id, sandbox)
    live = LiveSession(agent=agent, sandbox=sandbox, deps=deps, max_steps=max_steps)
    registry.put(session_id, live)
    set_status(session, session_id, RetestSessionStatus.STARTING)
    try:
        result = agent.run_sync(finding_prompt, deps=deps)
    except Exception as exc:  # noqa: BLE001 - orchestration boundary; record + tear down
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)


def apply_decision(
    session: Session, registry: SessionRegistry, session_id: int, *, approved: bool, reason: str = "",
) -> None:
    """Resume the paused run with an approval or denial."""
    live = registry.get(session_id)
    if live is None or live.pending_call_id is None:
        return
    kind = SessionEventKind.COMMAND_APPROVED if approved else SessionEventKind.COMMAND_REJECTED
    append_event(session, session_id, kind, {"reason": reason} if reason else {})
    live.step_count += 1
    if live.step_count > live.max_steps:
        record_verdict(session, session_id, VerdictStatus.INCONCLUSIVE, "budget exhausted")
        _mark_given_up(session, session_id)
        _teardown(registry, session_id)
        return
    set_status(session, session_id, RetestSessionStatus.RUNNING_COMMAND)
    results = DeferredToolResults()
    results.approvals[live.pending_call_id] = ToolApproved() if approved else ToolDenied(reason)
    live.pending_call_id = None
    try:
        result = live.agent.run_sync(
            deps=live.deps, message_history=live.messages, deferred_tool_results=results
        )
    except Exception as exc:  # noqa: BLE001
        _fail(session, registry, session_id, str(exc))
        return
    _dispatch_output(session, registry, session_id, result)


def end_session(session: Session, registry: SessionRegistry, session_id: int) -> None:
    """Operator-initiated end: tear down and mark ``ended`` (if not already terminal)."""
    record = session.get(RetestSessionRecord, session_id)
    if record is None or RetestSessionStatus(record.status) in _TERMINAL:
        return
    set_status(session, session_id, RetestSessionStatus.ENDED)
    _teardown(registry, session_id)


def _mark_given_up(session: Session, session_id: int) -> None:
    record = session.get(RetestSessionRecord, session_id)
    if record is not None:
        record.status = RetestSessionStatus.GIVEN_UP.value
        session.commit()
        append_event(session, session_id, SessionEventKind.STATE_CHANGE,
                     {"to": RetestSessionStatus.GIVEN_UP.value})


def _fail(session: Session, registry: SessionRegistry, session_id: int, detail: str) -> None:
    append_event(session, session_id, SessionEventKind.ERROR, {"detail": detail})
    set_status(session, session_id, RetestSessionStatus.ERROR)
    _teardown(registry, session_id)
```

> `record_verdict` sets `CONCLUDED`; the budget path overrides to `GIVEN_UP` via `_mark_given_up` after recording the inconclusive verdict — keep that ordering. Watch the xenon gate on `apply_decision`; if it trips C, extract the budget check into a helper.

- [ ] **Step 4: Run tests + gates**

Run: `uv run pytest tests/unit/test_retest_session.py -v && uv run mypy && uv run ruff check && uv run xenon --max-absolute C --max-modules B --max-average A src`
Expected: PASS / clean / complexity OK.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "feat(retest): orchestrator — step driver, approve/reject, budget give-up (FR-17)"
```

---

## Task 6: REST endpoints + DI wiring + background workers

**Files:**
- Modify: `src/revalid/app.py`
- Test: `tests/integration/test_retest_session_api.py`

**Interfaces:**
- Produces (HTTP): `POST /api/findings/{finding_id}/retest-session` → `202 RetestSessionOut`; `GET /api/retest-sessions/{id}` → `RetestSessionOut` (+ `events`); `POST /api/retest-sessions/{id}/commands/{cid}/approve`; `.../reject` (optional `{reason}`); `POST /api/retest-sessions/{id}/end`.
- Produces (DI): `get_retest_agent(settings) -> Agent[...]`; `RetestAgentDep`; `get_sandbox_factory() -> SandboxFactory`; `SandboxFactoryDep`.
- Produces (workers): `run_first_step(sessions, registry, session_id, agent, sandbox, prompt)`; `run_decision(sessions, registry, session_id, approved, reason)`.
- Consumes: Task 5 orchestrator, `_get_finding_or_404` (`app.py:409`), `current_version(...).to_domain()` (`findings.py:79`).

- [ ] **Step 1: Write the failing integration test** (dependency-override `FakeSandbox` + `FunctionModel`; POST → GET shows proposed → approve → GET shows verdict). Recall: Starlette `TestClient` runs BackgroundTasks to completion before each POST returns, so no polling is needed.

```python
# tests/integration/test_retest_session_api.py
import pytest
from fastapi.testclient import TestClient

from revalid.app import create_app, get_retest_agent, get_sandbox_factory
from revalid.db import IN_MEMORY, create_db_engine
from revalid.retest_agent import build_retest_agent
from revalid.sandbox import CommandResult, FakeSandbox
# reuse _script (agent test) via a shared helpers module or copy locally

pytestmark = pytest.mark.integration

_IMPORT = {"findings": [{"title": "SQLi login bypass", "description": "auth bypass"}]}


def _client() -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_retest_agent] = lambda: build_retest_agent(FunctionModel(_script))
    box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
    app.dependency_overrides[get_sandbox_factory] = lambda: (lambda: box)
    return TestClient(app)


def test_retest_session_flow_proposes_then_concludes_on_approval() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)  # match the real import route
        started = client.post("/api/findings/1/retest-session")
        assert started.status_code == 202
        sid = started.json()["id"]

        state = client.get(f"/api/retest-sessions/{sid}").json()
        assert state["status"] == "awaiting_command"
        proposed = [e for e in state["events"] if e["kind"] == "command_proposed"][0]
        cid = proposed["payload"]["tool_call_id"]

        approve = client.post(f"/api/retest-sessions/{sid}/commands/{cid}/approve")
        assert approve.status_code in (200, 202)

        final = client.get(f"/api/retest-sessions/{sid}").json()
        assert final["status"] == "concluded"
        assert final["verdict_status"] == "still_open"
        assert any(e["kind"] == "command_output" for e in final["events"])
```

> Confirm the finding-import route name/shape against the existing integration tests (`grep` `tests/integration/test_approval_api.py` for the import call). Use whatever they use.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_retest_session_api.py -v`
Expected: FAIL — routes/DI not defined.

- [ ] **Step 3: Add response models + DI + workers + route group** (mirror `PlanOut.from_record` `app.py:213`, `get_plan_agent` `app.py:349`, `run_plan_generation` `app.py:451`, the `_register_*_routes` preamble `app.py:753`). Build the registry in `create_app`.

```python
# src/revalid/app.py — response models (near PlanOut)
class SessionEventOut(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]


class RetestSessionOut(BaseModel):
    id: int
    finding_id: int
    status: str
    model: str
    verdict_status: str | None
    verdict_rationale: str | None
    events: list[SessionEventOut] = []

    @classmethod
    def from_record(cls, record: RetestSessionRecord, events: list[dict[str, Any]]) -> "RetestSessionOut":
        return cls(
            id=record.id, finding_id=record.finding_id, status=record.status, model=record.model,
            verdict_status=record.verdict_status, verdict_rationale=record.verdict_rationale,
            events=[SessionEventOut(**e) for e in events],
        )
```

```python
# src/revalid/app.py — DI (near get_plan_agent)
def get_retest_agent(settings: SettingsDep) -> Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]:
    """Yield the FR-17 retest agent built from the persisted setting (ADR-0021)."""
    return build_retest_agent(build_model(settings))


def get_sandbox_factory() -> SandboxFactory:
    """Yield a factory that builds a fresh Docker sandbox per session."""
    def factory() -> Sandbox:
        # session_id is not known here; DockerSandbox is constructed in the worker.
        raise SandboxUnavailableError("sandbox factory must be bound per session")
    return factory


RetestAgentDep = Annotated[Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests], Depends(get_retest_agent)]
SandboxFactoryDep = Annotated[SandboxFactory, Depends(get_sandbox_factory)]
```

> **Sandbox construction wrinkle:** `DockerSandbox` needs `session_id`, unknown at DI time. Resolve by making the production factory a *callable that takes the session id*: define `SandboxFactory = Callable[[int], Sandbox]` in `sandbox.py` and have `get_sandbox_factory` return `lambda sid: DockerSandbox(sid)`. Tests override with `lambda: (lambda _sid: FakeSandbox([...]))`. Update Task 2's `SandboxFactory` type accordingly (`Callable[[int], Sandbox]`) and Task 6's test override to `lambda: (lambda _sid: box)`.

```python
# src/revalid/app.py — background workers (near run_plan_generation)
def run_first_step(sessions, registry, session_id, agent, make_sandbox, prompt) -> None:
    with sessions() as session:
        try:
            sandbox = make_sandbox(session_id)
        except SandboxUnavailableError as exc:
            _fail(session, registry, session_id, str(exc))
            return
        start_and_step(session, registry, session_id, agent, sandbox, prompt)


def run_decision(sessions, registry, session_id, approved, reason) -> None:
    with sessions() as session:
        apply_decision(session, registry, session_id, approved=approved, reason=reason)
```

```python
# src/revalid/app.py — route group (register after _register_retest_routes)
def _register_session_routes(router, sessions, registry) -> None:
    def get_session():
        with sessions() as session:
            yield session
    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}/retest-session", response_model=RetestSessionOut, status_code=202)
    def start_retest_session(finding_id: int, background: BackgroundTasks, session: SessionDep,
                             agent: RetestAgentDep, make_sandbox: SandboxFactoryDep) -> RetestSessionOut:
        _get_finding_or_404(session, finding_id)
        version = current_version(session, finding_id)
        prompt = _finding_prompt(version.to_domain())  # small local helper: title+description+steps
        record = create_session(session, finding_id=finding_id, model=agent_model_name(agent))
        background.add_task(run_first_step, sessions, registry, record.id, agent, make_sandbox, prompt)
        return RetestSessionOut.from_record(record, [])

    @router.get("/retest-sessions/{session_id}", response_model=RetestSessionOut)
    def get_retest_session(session_id: int, session: SessionDep) -> RetestSessionOut:
        record = session.get(RetestSessionRecord, session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return RetestSessionOut.from_record(record, load_events_after(session, session_id, 0))

    @router.post("/retest-sessions/{session_id}/commands/{cid}/approve", status_code=202)
    def approve_command(session_id: int, cid: str, background: BackgroundTasks) -> dict[str, str]:
        background.add_task(run_decision, sessions, registry, session_id, True, "")
        return {"status": "approved"}

    @router.post("/retest-sessions/{session_id}/commands/{cid}/reject", status_code=202)
    def reject_command(session_id: int, cid: str, background: BackgroundTasks,
                       body: RejectRequest | None = None) -> dict[str, str]:
        background.add_task(run_decision, sessions, registry, session_id, False, body.reason if body else "")
        return {"status": "rejected"}

    @router.post("/retest-sessions/{session_id}/end", status_code=202)
    def end_retest_session(session_id: int, session: SessionDep) -> dict[str, str]:
        end_session(session, registry, session_id)
        return {"status": "ended"}
```

```python
# src/revalid/app.py — in create_app, after building `sessions`
registry = SessionRegistry()
app.state.registry = registry
# ...
_register_session_routes(api, sessions, registry)
```

> Add `RejectRequest(BaseModel)` (`reason: str = ""`), a `_finding_prompt(finding) -> str` helper, and the new imports (`create_session`, `load_events_after`, `start_and_step`, `apply_decision`, `end_session`, `SessionRegistry`, `_fail`, `RetestSessionRecord`, sandbox symbols). Keep each route function ≤ complexity C.

- [ ] **Step 4: Run tests + gates**

Run: `uv run pytest tests/integration/test_retest_session_api.py -v && uv run mypy && uv run ruff check && uv run xenon --max-absolute C --max-modules B --max-average A src`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(retest): REST endpoints + DI + background workers for sessions (FR-17)"
```

---

## Task 7: WebSocket stream (tail the transcript)

**Files:**
- Modify: `src/revalid/app.py` (add `@router.websocket`)
- Test: `tests/integration/test_retest_session_ws.py`

**Interfaces:**
- Produces: `WS /api/retest-sessions/{id}/stream` — on connect replays all events (`seq` order), then tails new events every `_WS_POLL_SECONDS` until the session is terminal, sending each as `{seq, kind, payload}` JSON.
- Consumes: `load_events_after`, `RetestSessionStatus`, `_TERMINAL`.

- [ ] **Step 1: Write the failing WS integration test** (`websocket_connect`; interleave a REST approve while the socket is open)

```python
# tests/integration/test_retest_session_ws.py
import pytest
pytestmark = pytest.mark.integration
# reuse _client() / _script / FakeSandbox setup from test_retest_session_api.py (share via a helper module)


def test_ws_streams_proposed_output_and_verdict() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        with client.websocket_connect(f"/api/retest-sessions/{sid}/stream") as ws:
            kinds: list[str] = []
            first = ws.receive_json()
            kinds.append(first["kind"])
            # drain until we see the proposal, then approve
            while "command_proposed" not in kinds:
                kinds.append(ws.receive_json()["kind"])
            cid = "0"  # cid is opaque to the endpoint; approval is by session
            client.post(f"/api/retest-sessions/{sid}/commands/{cid}/approve")
            while "verdict" not in kinds:
                kinds.append(ws.receive_json()["kind"])
    assert "command_proposed" in kinds
    assert "command_output" in kinds
    assert "verdict" in kinds
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_retest_session_ws.py -v`
Expected: FAIL — no websocket route.

- [ ] **Step 3: Implement the WS endpoint** (inside `_register_session_routes`; use `asyncio.sleep`; sync DB reads are fine for single-user local)

```python
# src/revalid/app.py — inside _register_session_routes
@router.websocket("/retest-sessions/{session_id}/stream")
async def stream_session(websocket: WebSocket, session_id: int) -> None:
    await websocket.accept()
    last_seq = 0
    try:
        while True:
            with sessions() as session:
                record = session.get(RetestSessionRecord, session_id)
                if record is None:
                    await websocket.close(code=1008)
                    return
                events = load_events_after(session, session_id, last_seq)
                terminal = RetestSessionStatus(record.status) in _TERMINAL
            for event in events:
                await websocket.send_json(event)
                last_seq = event["seq"]
            if terminal and not events:
                await websocket.close()
                return
            await asyncio.sleep(_WS_POLL_SECONDS)
    except WebSocketDisconnect:
        return
```

> Add `_WS_POLL_SECONDS = 0.25`, imports `from fastapi import WebSocket, WebSocketDisconnect`, `import asyncio`, and `from revalid.retest_session import _TERMINAL` (or export a public `is_terminal(status)` helper to avoid the underscore import — prefer the public helper).

- [ ] **Step 4: Run tests + gates**

Run: `uv run pytest tests/integration/test_retest_session_ws.py -v && uv run mypy && uv run ruff check`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_ws.py src/revalid/retest_session.py
git commit -m "feat(retest): websocket transcript stream (FR-17)"
```

---

## Task 8: Frontend — API client additions + `useRetestSession` hook

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/hooks/queryKeys.ts`
- Create: `frontend/src/hooks/useRetestSession.ts` (+ `useRetestSession.test.ts`)
- Modify: `frontend/package.json` (add `@xterm/xterm`), `frontend/vite.config.ts` (`proxy["/api"].ws = true`)

**Interfaces:**
- Produces (client): `type SessionEvent = { seq: number; kind: string; payload: Record<string, unknown> }`; `type RetestSession = { id: number; finding_id: number; status: string; model: string; verdict_status: string | null; verdict_rationale: string | null; events: SessionEvent[] }`; `startRetestSession(findingId)`, `getRetestSession(id)`, `approveCommand(id, cid)`, `rejectCommand(id, cid, reason?)`, `endRetestSession(id)`, `retestSocketUrl(id): string`.
- Produces (hook): `useRetestSession(id, makeSocket?) -> { events, status, verdict, connected }`.

- [ ] **Step 1: Add the client functions** (mirror `retest` at `client.ts:160`; add a WS URL helper since WS has no relative form)

```ts
// frontend/src/api/client.ts (append)
export interface SessionEvent { seq: number; kind: string; payload: Record<string, unknown>; }
export interface RetestSession {
  id: number; finding_id: number; status: string; model: string;
  verdict_status: string | null; verdict_rationale: string | null; events: SessionEvent[];
}

export const startRetestSession = (findingId: number) =>
  request<RetestSession>(`/findings/${String(findingId)}/retest-session`, { method: "POST" });
export const getRetestSession = (id: number) =>
  request<RetestSession>(`/retest-sessions/${String(id)}`);
export const approveCommand = (id: number, cid: string) =>
  request<{ status: string }>(`/retest-sessions/${String(id)}/commands/${cid}/approve`, { method: "POST" });
export const rejectCommand = (id: number, cid: string, reason = "") =>
  request<{ status: string }>(`/retest-sessions/${String(id)}/commands/${cid}/reject`, jsonInit("POST", { reason }));
export const endRetestSession = (id: number) =>
  request<{ status: string }>(`/retest-sessions/${String(id)}/end`, { method: "POST" });

export function retestSocketUrl(id: number): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${API_BASE}/retest-sessions/${String(id)}/stream`;
}
```

- [ ] **Step 2: Write the failing hook test** (inject a fake socket; feed events; assert accumulation + terminal verdict)

```ts
// frontend/src/hooks/useRetestSession.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useRetestSession } from "./useRetestSession";

class FakeSocket {
  onmessage: ((e: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = () => {};
  emit(event: unknown) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

describe("useRetestSession", () => {
  it("accumulates events and surfaces the verdict", async () => {
    const socket = new FakeSocket();
    const { result } = renderHook(() =>
      useRetestSession(1, () => socket as unknown as WebSocket));
    act(() => { socket.onopen?.(); });
    act(() => { socket.emit({ seq: 1, kind: "command_proposed", payload: { command: "id" } }); });
    act(() => { socket.emit({ seq: 2, kind: "verdict", payload: { status: "still_open", rationale: "x" } }); });
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.verdict?.status).toBe("still_open");
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useRetestSession.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the hook** (WS primary via injectable factory; dedupe by `seq`; derive status/verdict from events)

```ts
// frontend/src/hooks/useRetestSession.ts
import { useEffect, useRef, useState } from "react";
import { retestSocketUrl, type SessionEvent } from "../api/client";

export type SocketFactory = (url: string) => WebSocket;
export interface Verdict { status: string; rationale: string; }

export function useRetestSession(
  id: number,
  makeSocket: SocketFactory = (url) => new WebSocket(url),
) {
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const seen = useRef<Set<number>>(new Set());

  useEffect(() => {
    seen.current = new Set();
    setEvents([]);
    const socket = makeSocket(retestSocketUrl(id));
    socket.onopen = () => setConnected(true);
    socket.onerror = () => setConnected(false);
    socket.onmessage = (e: MessageEvent) => {
      const event = JSON.parse(e.data) as SessionEvent;
      if (seen.current.has(event.seq)) return;
      seen.current.add(event.seq);
      setEvents((prev) => [...prev, event]);
    };
    return () => socket.close();
  }, [id, makeSocket]);

  const verdictEvent = [...events].reverse().find((e) => e.kind === "verdict");
  const stateEvent = [...events].reverse().find((e) => e.kind === "state_change");
  return {
    events,
    connected,
    status: (stateEvent?.payload.to as string) ?? "starting",
    verdict: verdictEvent ? (verdictEvent.payload as unknown as Verdict) : null,
  };
}
```

- [ ] **Step 5: Add `@xterm/xterm` + enable WS proxy**

```bash
cd frontend && npm install @xterm/xterm
```
```ts
// frontend/vite.config.ts — in server.proxy["/api"]
server: { proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: true } } },
```

- [ ] **Step 6: Run tests + gates**

Run: `cd frontend && npx vitest run src/hooks/useRetestSession.test.ts && npm run lint && npx tsc --noEmit`
Expected: PASS / clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/hooks/ frontend/package.json frontend/package-lock.json frontend/vite.config.ts
git commit -m "feat(ui): retest-session API client + useRetestSession WS hook (FR-17)"
```

---

## Task 9: Frontend — the Retest Session view (terminal + approval card + verdict banner)

**Files:**
- Create: `frontend/src/components/RetestTerminal.tsx` (+ `RetestTerminal.test.tsx`)
- Create: `frontend/src/routes/RetestSession.tsx` (+ `RetestSession.test.tsx`)
- Modify: `frontend/src/App.tsx` (route), `frontend/src/routes/stages/RetestStage.tsx` (entry point)

**Interfaces:**
- `RetestTerminal({ lines }: { lines: string[] })` — appends lines to an `xterm` instance; isolated so the route is testable without a real terminal.
- `RetestSession()` — route reading `useParams` id, `useRetestSession`, rendering the terminal (from `command_output`/`command_proposed` events), the approval card when `status === "awaiting_command"` (reuse `ui/Button` `positive`/`danger`), and the verdict banner when a `verdict` event exists (reuse `VerdictCard`/`StatusBadge` + `lib/status.ts`).

- [ ] **Step 1: Write the failing route test** (mock the hook; assert approval card + click wiring + verdict banner). Mirror `stages.test.tsx` (`vi.mock`, `renderWithProviders`, `userEvent.click`).

```tsx
// frontend/src/routes/RetestSession.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { RetestSession } from "./RetestSession";
import * as hook from "../hooks/useRetestSession";
import * as client from "../api/client";

vi.mock("../hooks/useRetestSession");
vi.mock("../api/client");

function renderAt(id = 1) {
  return render(
    <MemoryRouter initialEntries={[`/retest-sessions/${id}`]}>
      <Routes><Route path="/retest-sessions/:id" element={<RetestSession />} /></Routes>
    </MemoryRouter>,
  );
}

describe("RetestSession", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows the approval card and approves", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "command_proposed",
                 payload: { command: "curl ...", rationale: "retry", tool_call_id: "abc" } }],
      status: "awaiting_command", verdict: null, connected: true,
    } as never);
    vi.mocked(client.approveCommand).mockResolvedValue({ status: "approved" });
    renderAt(1);
    expect(screen.getByText(/retry/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(client.approveCommand).toHaveBeenCalledWith(1, "abc");
  });

  it("renders the verdict banner", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 2, kind: "verdict", payload: { status: "still_open", rationale: "bypassable" } }],
      status: "concluded",
      verdict: { status: "still_open", rationale: "bypassable" }, connected: true,
    } as never);
    renderAt(1);
    expect(screen.getByText(/bypassable/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/routes/RetestSession.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `RetestTerminal.tsx`** (guard `xterm` behind an effect; write `lines` incrementally)

```tsx
// frontend/src/components/RetestTerminal.tsx
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

export function RetestTerminal({ lines }: { lines: string[] }) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const written = useRef(0);

  useEffect(() => {
    if (host.current && !term.current) {
      term.current = new Terminal({ convertEol: true, fontFamily: "var(--font-mono)", disableStdin: true });
      term.current.open(host.current);
    }
  }, []);
  useEffect(() => {
    const t = term.current;
    if (!t) return;
    for (let i = written.current; i < lines.length; i++) t.writeln(lines[i]);
    written.current = lines.length;
  }, [lines]);

  return <div ref={host} data-testid="retest-terminal" className="h-80 overflow-hidden rounded-md" />;
}
```

- [ ] **Step 4: Implement `RetestSession.tsx`** (compose terminal lines from events; approval card; verdict banner; End button — reuse `ui/Button`, `VerdictCard`, `Panel`)

```tsx
// frontend/src/routes/RetestSession.tsx
import { useParams } from "react-router-dom";
import { approveCommand, endRetestSession, rejectCommand, type SessionEvent } from "../api/client";
import { useRetestSession } from "../hooks/useRetestSession";
import { RetestTerminal } from "../components/RetestTerminal";
import { Button } from "../components/ui/Button";
import { Panel, PanelHeader } from "../components/ui/Panel";
import { StatusBadge } from "../components/StatusBadge";

function toLines(events: SessionEvent[]): string[] {
  const out: string[] = [];
  for (const e of events) {
    if (e.kind === "command_proposed") out.push(`$ ${String(e.payload.command)}`);
    if (e.kind === "command_output") out.push(String(e.payload.stdout ?? ""), String(e.payload.stderr ?? ""));
  }
  return out.filter(Boolean);
}

export function RetestSession() {
  const id = Number(useParams().id);
  const { events, status, verdict } = useRetestSession(id);
  const proposed = [...events].reverse().find((e) => e.kind === "command_proposed");
  const cid = proposed ? String(proposed.payload.tool_call_id) : "";

  return (
    <div className="space-y-4">
      <PanelHeader eyebrow="Agentic retest" aside={<StatusBadge status={status as never} />} />
      <RetestTerminal lines={toLines(events)} />

      {status === "awaiting_command" && proposed && (
        <Panel>
          <p className="font-mono text-sm">{String(proposed.payload.command)}</p>
          <p className="text-sm opacity-80">{String(proposed.payload.rationale)}</p>
          <div className="mt-3 flex gap-2">
            <Button variant="positive" onClick={() => approveCommand(id, cid)}>Approve</Button>
            <Button variant="danger" onClick={() => rejectCommand(id, cid)}>Reject</Button>
          </div>
        </Panel>
      )}

      {verdict && (
        <Panel>
          <StatusBadge status={verdict.status as never} />
          <p className="mt-2 text-sm">{verdict.rationale}</p>
        </Panel>
      )}

      <Button variant="ghost" onClick={() => endRetestSession(id)}>End session</Button>
    </div>
  );
}
```

- [ ] **Step 5: Wire the route + entry point**

```tsx
// frontend/src/App.tsx — add import + route
<Route path="/retest-sessions/:id" element={<RetestSession />} />
```
```tsx
// frontend/src/routes/stages/RetestStage.tsx — add a start button that calls
// startRetestSession(findingId) then navigate(`/retest-sessions/${session.id}`)
```

- [ ] **Step 6: Run tests + gates**

Run: `cd frontend && npx vitest run && npm run lint && npx tsc --noEmit && npm run build`
Expected: PASS / clean / build OK. (Terminal DOM is jsdom-mocked; `xterm`'s `open` is a no-op under jsdom — if it throws, guard with `try/catch` in the effect and assert on the approval card/verdict only.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/RetestTerminal.tsx frontend/src/routes/RetestSession.tsx frontend/src/App.tsx frontend/src/routes/stages/RetestStage.tsx frontend/src/**/*.test.tsx
git commit -m "feat(ui): agentic retest session view — terminal, approval card, verdict (FR-17)"
```

---

## Task 10: System test — real Docker sandbox + egress-lock assertion + demo

**Files:**
- Create: `tests/system/test_retest_session_system.py`
- Modify: `lab/docker-compose.yml` (name the lab network / confirm container name), `.github/workflows/system-tests.yml` (`--extra sandbox`)
- Create: `scripts/demo/retest_session.py`; Modify: `Makefile` (`demo-retest-session`)

**Interfaces:**
- Consumes: `DockerSandbox`, `egress_probe_command`, the real lab.

- [ ] **Step 1: Write the system test** (self-skip when Docker/lab unavailable — the `browser.py` graceful-skip precedent; assert lab reachable + non-lab host unreachable + a scripted retest concludes)

```python
# tests/system/test_retest_session_system.py
import pytest

from revalid.sandbox import DockerSandbox, egress_probe_command

pytestmark = pytest.mark.system


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.mark.system
def test_sandbox_can_reach_lab_but_not_the_internet() -> None:
    if not _docker_available():
        pytest.skip("docker not available; run with the sandbox extra + a running daemon")
    box = DockerSandbox(session_id=9999)
    box.start()
    try:
        lab = box.exec("curl --max-time 5 -s -o /dev/null -w '%{http_code}' "
                       "http://revalid-juice-shop:3000/rest/admin/application-version", timeout=15)
        assert lab.exit_code == 0  # lab is a network member -> reachable

        egress = box.exec(egress_probe_command("example.com"), timeout=15)
        assert egress.exit_code != 0  # internet is NOT reachable (egress lock, NFR-03)
    finally:
        box.stop()
```

- [ ] **Step 2: Confirm the lab container name + network** — ensure `lab/docker-compose.yml` sets `container_name: revalid-juice-shop` (it does) so `network.connect("revalid-juice-shop")` resolves. No `--internal` network is added to the compose file; `DockerSandbox` creates the per-session internal network at runtime and connects the lab container into it.

- [ ] **Step 3: Provision the extra in CI**

```yaml
# .github/workflows/system-tests.yml — extend the sync step
- run: uv sync --locked --extra browser --extra sandbox
```

- [ ] **Step 4: Add the demo** (offline-capable: uses `FakeSandbox` + a `FunctionModel` to show the full proposed→approve→verdict cycle without Docker, matching `demo-browser-xss`)

```python
# scripts/demo/retest_session.py — build an in-memory app, drive the cycle, print the transcript
```
```make
# Makefile (.PHONY += demo-retest-session)
demo-retest-session:
	uv run python scripts/demo/retest_session.py
```

- [ ] **Step 5: Run locally (best-effort) + gates**

Run: `uv run pytest -m system -k retest_session --no-cov` (skips without Docker) and `make demo-retest-session`.
Expected: system test skips or passes against a running lab; demo prints a `still_open` transcript offline.

- [ ] **Step 6: Commit**

```bash
git add tests/system/test_retest_session_system.py .github/workflows/system-tests.yml scripts/demo/retest_session.py Makefile
git commit -m "test(retest): egress-lock system test + offline demo (FR-17)"
```

---

## Task 11: Process artifacts — ADR-0025, FR-17 in SRS, roadmap M6 note

These land in the **Slice 0 PR** (spec §11), pointing back at the design spec.

**Files:**
- Create: `docs/adr/0025-agentic-retest-console.md` (use the `adr` skill; status `proposed`)
- Modify: `docs/requirements/srs.md` (add **FR-17** umbrella + Slice 0 acceptance criteria; use the `requirements` skill)
- Modify: `docs/roadmap.md` (append a **2026-07-16 M6** "Current state" note; add an **M6** milestone section)

- [ ] **Step 1: ADR-0025** — record: sandboxed HITL agentic retest; shared terminal (read-only in Slice 0); agent-determined verdict + transcript audit; the NFR-02 reproducibility shift; that it supersedes the FR-04/05/07-09 execution model over time. Reference the spec + epic #87.

- [ ] **Step 2: FR-17 in the SRS** — umbrella requirement; Slice 0 acceptance criteria: (AC1) a session runs one approved command in an egress-locked sandbox and yields a verdict; (AC2) no command executes before human approval; (AC3) the transcript is append-only and replayable; (AC4) a non-lab host is unreachable from the sandbox.

- [ ] **Step 3: roadmap M6 note** — mirror the existing dated-entry style; state Slice 0 landed, the old batch path stays until Slice 5, and the build-order table.

- [ ] **Step 4: Verify docs build**

Run: `make docs` (if it validates markdown/diagrams) or at minimum `uv run mkdocs build` if wired.
Expected: no broken references.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0025-agentic-retest-console.md docs/requirements/srs.md docs/roadmap.md
git commit -m "docs: ADR-0025 + FR-17 SRS + M6 roadmap note for the agentic retest console"
```

---

## Task 12: Full-suite verification + PR

- [ ] **Step 1: Run every gate the way CI does**

```bash
uv run ruff check && uv run ruff format --check
uv run mypy
uv run xenon --max-absolute C --max-modules B --max-average A src
uv run pytest -m "not integration and not system" --cov --cov-report=term-missing   # ≥ 80%
uv run pytest -m integration --no-cov
cd frontend && npm run lint && npx tsc --noEmit && npx vitest run && npm run build
```
Expected: all green; backend coverage ≥ 80 % (new logic modules ~100 % of non-live lines).

- [ ] **Step 2: Drive it end-to-end in a browser** (per the `verify` skill / project verify precedent) — `make lab-up`, start the app, start a session on a real finding on a live Ollama, approve one command, observe the streamed output + verdict, dark + light, zero console errors. Record the observation in the PR "How to validate".

- [ ] **Step 3: Open the PR** — body includes `Closes #<n>`, the "How to validate" commands above, and the acceptance checkboxes (FR-17 AC1–AC4). Queue auto-merge once required CI is green.

---

## Self-review notes (author)

- **Spec coverage:** sandbox (Task 2) ✓ · orchestrator + state machine + transcript (Tasks 3, 5) ✓ · agent `run_command` gated + verdict (Task 4) ✓ · WS + REST approve/reject/end (Tasks 6, 7) ✓ · read-only terminal UI + approval card + verdict banner (Task 9) ✓ · egress lock + its test (Tasks 2, 10) ✓ · new tables (Task 3) ✓ · verdict-inside-session, no `Verdict`/`Evidence` reshaping (Task 3, deferred to Slice 5) ✓ · process artifacts (Task 11) ✓ · testing strategy unit/integration/system (throughout) ✓.
- **Deferred per spec:** human terminal input (Slice 1), plan panel (2), chat (3), free-launch + budget UI (4 — budget backstop exists in Task 5 but has no UI), verdict adjudication + FR-09/10/12 integration (5), command *editing* before approval (approve/reject only). Not in this plan by design.
- **Type consistency to watch when executing:** `SandboxFactory` becomes `Callable[[int], Sandbox]` (Task 6 wrinkle) — apply in Task 2. `is_terminal()` public helper (Task 7) instead of importing `_TERMINAL`. `ConcludeOutput` reuses `VerdictStatus` (not a new enum). The stateful `FunctionModel` (`_script`) is shared across Tasks 4/5/6/7 — factor it into a `tests/_retest_helpers.py` (or `tests/conftest.py` fixture) rather than copying.
- **Version risk:** the exact `pydantic-ai` deferred-tool resume signature (`ToolApproved`/`ToolDenied`, no-prompt `run_sync` on resume) is verified in Task 4 Step 3 before anything is built on it — the spec's "validate the gate first" mandate.
