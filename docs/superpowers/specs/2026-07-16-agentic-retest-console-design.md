# Agentic Retest Console — design (vision + Slice 0)

- **Date**: 2026-07-16
- **Status**: draft (brainstorm output, approved by Álvaro to spec Slice 0)
- **Owner**: Álvaro Navarro
- **Proposes**: milestone **M6**, requirement **FR-17**, **ADR-0025** (all land, `proposed`, with the Slice 0 implementation PR)
- **Supersedes over time**: the FR-04/FR-05/FR-07/FR-08/FR-09 *batch-plan* execution model
- **Epic**: [#87](https://github.com/SelfishCoconut/revalid/issues/87)

---

## 1. Motivation

Today's retest flow (FR-04 → FR-05 → FR-07/08/09): the LLM emits a **fixed, typed `RetestPlan`** up front; the human approves the whole batch; a deterministic engine executes it and derives each verdict as a pure function of one request's evidence.

Live use (roadmap, 2026-07-16) exposed the failure mode: a single weak LLM guess — e.g. the SQLi payload `' OR '1'='1'` with a real password instead of `' OR 1=1--` — spoils the batch and yields a **false `fixed`**, forcing regenerate-and-retry cycles. One-shot planning cannot observe a response and adapt mid-retest.

Álvaro's direction: let the LLM **roam and self-correct** — reason → run a command → observe → decide the next — but keep the human firmly in control by approving **every command before it runs** (command + a brief rationale) and **every change to the guiding plan**, always. The human can also drive the shell directly.

## 2. Vision — the Agentic Retest Console

Per finding, an interactive session:

- an **egress-locked ephemeral sandbox** (Docker, pinned image, pentest tools) reachable *only* to allowlisted lab targets;
- a **guiding plan** the agent proposes and maintains — **every plan change is human-approved**, always, even in free-launch mode;
- one **shared terminal** (web `xterm.js` ↔ WebSocket ↔ container PTY): agent and human type into the *same* shell and see the same live output. The agent's commands are **gated** (proposed as command + rationale → approve/edit/reject → written into the shared PTY); the human's keystrokes are **direct/ungated**; and the agent **observes the human's manual commands** and factors them into its reasoning;
- a **free-launch** toggle to let the agent auto-run commands (plan changes stay gated);
- a **chat** view to steer the agent ("focus on the login endpoint") or ask it questions ("what did that 500 mean?");
- ends when the agent **determines the status**, the human **ends** it, or the agent **gives up** (a step/wall-clock budget backstop);
- the **verdict** is agent-determined (human-overridable); the **command-history transcript is the audit trail**.

Three steering channels: **chat** (tell it), **approve/edit** (vet it), **type** (show it).

### Layout — chat-centric console (decided 2026-07-16)

The console reads as **a chat with the model**, not a terminal with controls bolted on. The **center column is the chat** — the agent's voice: its rationale, each gated command rendered as a chat card with **inline approve/reject**, and the verdict, as one scrolling conversation (`agent_message` events are reserved for future agent prose; from the chat-input slice the human's own messages join the same stream). The **terminal docks to the bottom** as a collapsible, read-only panel showing only *executed*-command output. This **supersedes Slice 0's terminal-centric arrangement** (read-only terminal on top, approval card + verdict stacked beneath). Operator interaction with the shell lands in a later slice — but **not** as the "one shared terminal" PTY bullet above: after researching how comparable tools do it, Álvaro chose discrete execs + a Claude-Code-style `!` command over a shared PTY (**ADR-0026** supersedes that bullet for the retest console). The reorientation itself is presentation-only (no API/orchestrator/sandbox change). This decision is carried into the slice order below and recorded as an update note in ADR-0025.

### Reproducibility (NFR-02) — an explicit shift

Today a verdict is a pure function of one request's evidence, so it re-derives offline. Under the agentic model the verdict is a **human-adjudicated agent judgment**, and reproducibility means an **append-only session transcript** — plan versions, every command + stdout/stderr/exit/timing, every human decision, and the agent's reasoning — that is **replayable and inspectable**, not a deterministic recomputation. This is a **stronger human-in-the-loop contribution** but a **weaker deterministic-reproducibility claim** than the current model; it will be stated plainly in the thesis (Design + Evaluation chapters), not papered over.

## 3. Build order (sub-projects)

Walking-skeleton-first (mirrors how M1 was built). Each slice is its own issue → plan → PR.

Revised 2026-07-16 to insert the chat-centric console shell (§2 Layout) as Slice 1;
the capability slices shift down one, and the former "chat steering / Q&A" is
reframed as the chat **input** slice (the chat *view* now arrives in Slice 1).

| Slice | Adds |
|------:|------|
| **0** (shipped) | skeleton: egress-locked container + Pydantic-AI agent with one gated `run_command` + live **read-only** terminal + one approval card → agent proposes a verdict |
| **1** | **chat-centric console shell** — chat becomes the center column (agent rationale → gated command card with inline approve/reject → verdict); the terminal docks to the bottom as a collapsible read-only output panel. Presentation only; steering stays approve/reject. |
| 2 | operator manual commands — `!<command>` runs a one-shot command in the sandbox (discrete exec, **not** a shared PTY — ADR-0026); the agent observes it on its next turn |
| 3 | plan panel — initial plan + gated plan updates |
| 4 | chat **input** / steering & Q&A — human types messages into the center chat to redirect the agent or ask about what it observed |
| 5 | free-launch mode + session controls + step/time budget + give-up |
| 6 | verdict adjudication + FR-10 audit / FR-12 export integration; retire the old batch path |

The old structured-plan path (`plan.py` / `approval.py` / `retest.py`) **stays fully operational** until Slice 6 — no big-bang removal.

---

## 4. Slice 0 — scope

**Goal / done-when:** From a finding, the operator starts a retest session; a sandboxed agent proposes **one** shell command + rationale; the operator approves it; it runs in an egress-locked container; the agent sees the output (streamed live to a web terminal) and proposes a verdict. **One approve → one exec → verdict.** This deliberately exercises the four risky pieces together: **sandbox, gated exec, live streaming, agent loop.**

**In scope:** sandbox runtime; session orchestrator + state machine + append-only transcript; agent with `run_command` (gated) + `conclude`; WebSocket streaming + REST approve/reject/end; a minimal UI (read-only terminal + approval card + verdict banner); tests (unit + integration with fakes, one nightly system test).

**Deferred (minimal placeholders only):** chat-centric layout (Slice 1), human terminal input / shared PTY (Slice 2), plan panel (3), chat input (4), free-launch + budget UI (5), verdict adjudication + FR-09/10/12 integration (6). **Command *editing* before approval is also deferred** — Slice 0 is approve/reject only; the human refines by rejecting (with a reason) or, from Slice 2, by typing their own command.

**Non-goals for Slice 0:** replacing the old plan path; multi-session concurrency tuning; a full pentest toolset image; reshaping the `Verdict`/`Evidence` domain model (Slice 6).

---

## 5. Architecture

Each unit has one job and a narrow interface:

- **`src/revalid/sandbox.py` — `Sandbox`**: `start()` → container from a pinned image on an egress-locked network; `exec(command, *, timeout)` → `CommandResult(stdout, stderr, exit_code, elapsed_ms)`; `stop()`. Ephemeral (one per session). A `FakeSandbox` with the same interface backs unit/integration tests (no Docker).
- **`src/revalid/retest_session.py` — orchestrator**: owns the session **state machine**, drives the agent, and persists the transcript. States: `starting → thinking → awaiting_command → running_command → (thinking | concluded | given_up | ended | error)`.
- **`src/revalid/retest_agent.py` — the agent**: a Pydantic AI `Agent` (model from the ADR-0021 settings). Tools: `run_command(command, rationale)` (gated) and `conclude(status, rationale)` (terminal).
- **`app.py`** — REST endpoints + the WebSocket stream.
- **frontend `src/routes/.../RetestSession.tsx`** — `xterm.js` terminal (read-only), the pending-command approval card, the verdict banner, an "End session" button.

### Data flow

```mermaid
sequenceDiagram
  actor Operator
  participant UI as SPA (terminal + card)
  participant API as FastAPI orchestrator
  participant Agent as Pydantic AI agent
  participant Box as Sandbox (Docker, egress-locked)

  Operator->>API: POST /findings/{id}/retest-session
  API->>Box: start() (ephemeral container)
  API->>Agent: run(goal = finding context)
  UI->>API: WS connect /retest-sessions/{id}/stream
  Agent->>API: tool run_command(cmd, rationale)  (suspends)
  API-->>UI: event: command_proposed {cmd, rationale}
  Operator->>API: POST .../commands/{cid}/approve
  API->>Box: exec(cmd)
  Box-->>API: stdout/stderr/exit (streamed)
  API-->>UI: event: command_output (live)
  API->>Agent: tool result = captured output
  Agent->>API: tool conclude(status, rationale)
  API-->>UI: event: verdict {status, rationale}
  API->>Box: stop()
```

## 6. Data model (new tables)

- **`retest_sessions`**: `id`, `finding_id` (FK → finding *identity*, FR-16), `status` (enum), `model` (resolved LLM string), `verdict_status` (nullable), `verdict_rationale` (nullable), `created_at`, `ended_at`.
- **`session_events`** (append-only transcript / audit): `id`, `session_id`, `seq`, `kind` (`agent_message` \| `command_proposed` \| `command_approved` \| `command_rejected` \| `command_output` \| `state_change` \| `verdict` \| `error`), `payload` (JSON), `created_at`.

**Verdict linkage — deferred on purpose.** The existing `Verdict`/`Evidence` domain model is request/response-shaped and cannot represent a multi-command shell conclusion. Slice 0 records the verdict **inside the session** (`retest_sessions.verdict_status/rationale` + a `verdict` event whose payload cites the commands). Wiring the agentic verdict into the FR-09 `verdicts` table / FR-10 audit / FR-12 export — including any `Evidence` reshaping — is **Slice 6**.

## 7. Safety & invariants

- **Egress lock (NFR-03):** the sandbox joins a Docker **`--internal`** network to which only the allowlisted lab container(s) are attached — it is *physically unable* to route to the internet or the host. The FR-06 allowlist becomes **network membership**. A test asserts a non-lab host is unreachable from inside the sandbox.
- **Ephemeral:** fresh container per session from a pinned image, torn down on end/error; nothing persists in the container — the transcript is the only durable artifact.
- **Traceability:** session → finding *identity* (versioned, FR-16); verdict provenance = the session id + transcript.
- **Resource bounds:** per-command timeout, session step/wall-clock budget (backs "give up"), output-size cap per command.
- **Threat model unchanged (ADR-0008):** single trusted user, app bound to `127.0.0.1`; the human approves each command; free-form shell is contained by the sandbox, not trusted.

## 8. API surface (Slice 0)

- `POST /api/findings/{id}/retest-session` → `202 {session_id}` (starts container + agent in the background, mirroring the FR-11 ingest / ADR-0022 plan-gen async pattern).
- `GET /api/retest-sessions/{id}` → session state + latest events (poll fallback / reconnect).
- `WS /api/retest-sessions/{id}/stream` → ordered events `{seq, kind, payload}`.
- `POST /api/retest-sessions/{id}/commands/{cid}/approve` \| `/reject` (reject carries an optional reason).
- `POST /api/retest-sessions/{id}/end`.

## 9. Agent design

Pydantic AI `Agent`, deps = the sandbox handle + session context. **System prompt** frames the retest goal from the finding (title, description, reproduction steps, affected endpoints) and the constraints: lab-only, prefer non-destructive, **one command at a time with a rationale**, conclude when confident or give up. **Tools:**

- `run_command(command: str, rationale: str) -> CommandResult` — **the gate.** Calling it suspends the agent on an approval future the orchestrator resolves from the REST approve/reject. On approve → execute in the sandbox, stream output, return the captured result. On reject → return the human's reason so the agent adapts.
- `conclude(status: still_open | fixed | inconclusive, rationale: str)` — terminal; ends the loop.

**Budget:** max *N* commands / *M* minutes → the orchestrator forces `conclude(inconclusive, "budget exhausted")` (the "give up" backstop).

**Gating mechanism risk:** the suspend-on-approval gate will be validated first thing in Slice 0. Primary approach: a gated tool that awaits an `asyncio` future. Fallback if Pydantic AI's tool ergonomics fight it: an **orchestrator-driven step loop** that calls the model one turn at a time and mediates the tool call itself. The impl plan picks the concrete pattern (see the `ai:building-pydantic-ai-agents` skill).

## 10. Testing strategy

- **Unit** (`tests/unit/`): `FakeSandbox` (scripted outputs, no Docker); agent via Pydantic AI **TestModel/FunctionModel** scripting `run_command → conclude`; state-machine transitions; budget → give-up.
- **Integration** (`tests/integration/`, marker `integration`): orchestrator + `FakeSandbox` + TestModel exercised over the real REST + WebSocket surface.
- **System** (`tests/system/`, marker `system`, nightly): a **real** Docker sandbox + the real lab; one scripted retest asserts a verdict is produced and that a non-lab host is unreachable from the sandbox. Provisioned in `system-tests.yml`.
- **Coverage:** `src/` ≥ 80%; new pure/logic modules aim for 100% of non-live lines (matching the `sandbox`/`browser` precedent — live Docker/PTY lines excluded).

## 11. Process & documentation

- **Milestone M6 — Agentic interactive retest** (created).
- **FR-17** (umbrella) added to `docs/requirements/srs.md`; per-slice acceptance criteria filled in as slices land.
- **ADR-0025** (`proposed`) records the architecture: sandboxed HITL agentic retest, shared terminal, agent-determined verdict + transcript audit; notes it supersedes the FR-04/05/07-09 execution model over time.
- Per the brainstorm checkpoint, **ADR-0025 + FR-17 + the M6 roadmap note land in the Slice 0 implementation PR**, pointing at this spec. The old batch path stays until Slice 6.

## 12. Open questions / risks

1. **Pydantic AI HITL gate** ergonomics (§9) — validated first in Slice 0; documented fallback.
2. **Docker in CI** for the system test — the lab is already dockerized in `system-tests.yml`, so the sandbox + `--internal` network fit the same job.
3. **WebSocket lifecycle** in a single-user local app — keep it minimal (127.0.0.1, no auth, reconnect via the `GET` poll fallback).
4. **Verdict/Evidence reshaping** — deliberately deferred to Slice 6; Slice 0 keeps the verdict inside the session.
5. **Egress-lock verification** — a concrete test that the sandbox cannot reach a non-lab host is part of Slice 0's definition of done.
