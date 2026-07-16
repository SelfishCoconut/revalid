# 0025. Agentic retest console (Slice 0)

Date: 2026-07-16
Status: proposed

## Context

The retest engine has, since M1, followed one model: an LLM proposes a
**fixed, typed `RetestPlan`** up front (FR-04), the human approves the whole
batch (FR-05), and a deterministic engine executes it, deriving each verdict as
a pure function of one request's evidence (FR-07/08/09). Live use (roadmap,
2026-07-16) exposed its failure mode: a single weak LLM guess — e.g. the SQLi
payload `' OR '1'='1'` with a real password instead of `' OR 1=1--` — spoils
the whole batch and yields a **false `fixed`**, forcing regenerate-and-retry
cycles. One-shot planning cannot observe a response and adapt mid-retest.

Álvaro's direction (design spec:
`docs/superpowers/specs/2026-07-16-agentic-retest-console-design.md`, epic
[#87](https://github.com/SelfishCoconut/revalid/issues/87)): let the LLM
**roam and self-correct** — reason → run a command → observe → decide the
next — but keep the human firmly in control by approving **every command**
before it runs, always. This ADR records **Slice 0** — the walking skeleton —
implemented in
`docs/superpowers/plans/2026-07-16-agentic-retest-console-slice-0.md` (issue
[#88](https://github.com/SelfishCoconut/revalid/issues/88)).

Forces:

- **Every command must be human-approved before execution — no exception,
  even in a future free-launch mode** (per the spec's vision). Slice 0 has no
  free-launch yet, but the gating mechanism must not paint that into a corner.
- **The sandbox must be safe by construction, not by policy.** The FR-06
  allowlist works for typed HTTP actions; a free-form shell is a much bigger
  attack surface and needs a stronger guarantee than "the model was told not
  to."
- **The audit trail is load-bearing (FR-10, NFR-02).** Today a verdict
  re-derives *offline* from stored evidence via a pure function
  (`audit.rederive_run`, ADR-0015). An agent that runs an open-ended sequence
  of shell commands cannot be re-derived the same way — the audit story has to
  change honestly, not get quietly weakened.
- **The old batch path (`plan.py`/`approval.py`/`retest.py`) must keep
  working.** This is a walking-skeleton build (mirrors how M1 was built,
  slice by slice); there is no room for a big-bang replacement mid-thesis.
- **Single-user threat model (ADR-0008) still applies.** No new auth ceremony;
  the sandbox's safety comes from network isolation, not from hardening the
  app itself.

## Decision

Build a **sandboxed, human-in-the-loop, agentic interactive retest console**.
Slice 0 ships the walking skeleton: from a finding, the operator starts a
session; a sandboxed agent proposes **one** shell command + rationale; the
operator approves it; it runs in an egress-locked Docker container; the agent
observes the output (streamed to a read-only web terminal) and concludes a
verdict. **One approve → one exec → verdict.** This exercises the four risky
pieces together: sandbox, gated exec, live streaming, agent loop.

### 1. Gating = Pydantic AI deferred tools

`src/revalid/retest_agent.py` builds one Pydantic AI `Agent` with:

- a single tool, `run_command(command, rationale)`, declared
  `@agent.tool(requires_approval=True)`;
- `output_type=[ConcludeOutput, DeferredToolRequests]` — every step ends
  either by proposing a command (the run *returns* a `DeferredToolRequests`
  and pauses, no blocked thread) or by emitting the final structured
  `ConcludeOutput` verdict (`status: VerdictStatus`, `rationale: str`).

The orchestrator (`src/revalid/retest_session.py`) drives the loop
step-by-step: `start_and_step` runs the first turn; when the model proposes a
command, the run result carries a `DeferredToolRequests` and the orchestrator
persists a `command_proposed` transcript event and waits. The human's
approve/reject over REST resumes the run via `apply_decision`, which builds
`DeferredToolResults` with `ToolApproved()` / `ToolDenied(reason)` and calls
`agent.run_sync(message_history=..., deferred_tool_results=...)` again. No
command executes until that resume happens.

This is **one gated tool + a structured verdict output**, not the two tools
(`run_command` + `conclude`) the design spec sketched — concluding is emitting
the final structured output, not a second tool call. Simpler, and avoids a
redundant terminal tool. *Alternative rejected:* a background thread blocked
on a `threading.Event` until approval — more code, a blocked OS thread per
pending session, no framework support for resuming with tool results.

### 2. Egress-locked sandbox

`src/revalid/sandbox.py` defines a `Sandbox` protocol (`start`/`exec`/`stop`)
plus:

- `FakeSandbox` — a scripted in-memory stand-in (list of `CommandResult`s or a
  callable) that backs unit/integration tests, no Docker required;
- `DockerSandbox` — a real ephemeral container: one Docker **`--internal`**
  network per session (`internal_network_name`), created fresh, with **only
  the allowlisted lab container** (`DEFAULT_LAB_CONTAINER`,
  `revalid-juice-shop`) attached to it, plus the sandbox container itself
  (pinned image `curlimages/curl:8.11.1`). An internal network has no route to
  the host or the internet — the sandbox is **physically unable** to reach
  anything but the lab, not merely told not to. The **FR-06 allowlist becomes
  network membership** for this execution path.

This is proven, not assumed: `tests/system/test_retest_session_system.py`
starts a live `DockerSandbox`, asserts a curl to the lab container succeeds
(`exit_code == 0`) and a curl to `example.com` fails (`exit_code != 0`) —
run against the real Docker daemon + lab, nightly (`system-tests.yml`), and
confirmed live during Slice 0 development.

Docker access is the `docker` Python SDK, gated behind an **optional
`sandbox` extra** (`pyproject.toml`: `sandbox = ["docker>=7.0"]`), lazily
imported inside `sandbox.py` and raising `SandboxUnavailableError` (→ HTTP 501)
when absent — mirroring the `browser`/Playwright precedent (ADR-0018,
`browser.py`). Live Docker lines are `# pragma: no cover`, covered only by the
system test. *Alternative rejected:* shelling out to the `docker` CLI via
`subprocess` — stringly-typed, draws ruff `S603`/`S607`, and there is no
`subprocess` use in `src/` today.

### 3. Transcript & streaming

- **`session_events`** (`src/revalid/db.py`) is an **append-only** table:
  `id`, `session_id`, `seq` (monotonic per session), `kind`
  (`agent_message` | `command_proposed` | `command_approved` |
  `command_rejected` | `command_output` | `state_change` | `verdict` |
  `error`), `payload` (JSON), `created_at`. It is the durable audit trail —
  every proposed/approved/rejected command, its captured output, every state
  transition, and the final verdict are rows here, ordered by `seq` so the
  transcript **replays** deterministically regardless of timestamp
  resolution.
- A **WebSocket** (`WS /api/retest-sessions/{id}/stream`) tails this table —
  Slice 0's source is a ~250 ms DB-poll of `session_events`, not an in-process
  pub/sub broker; the wire *interface* (ordered `{seq, kind, payload}`
  frames) is the forward-compatible part later slices' true char-streaming
  PTY will reuse. *Alternative rejected:* an async broker fed from sync
  background threads — needs `loop.call_soon_threadsafe` bridging, fragile
  for Slice 0's needs.
- **Live agent state** — message history, the sandbox handle, and budget
  counters (`SessionRegistry` / `LiveSession` in `retest_session.py`) — is an
  **in-memory, process-local registry**, deliberately ephemeral: a process
  restart abandons any in-flight session (its sandbox leaks until the next
  `start()` self-heals the stale network; see `DockerSandbox._clear_stale_network`),
  but the `session_events` transcript survives untouched. *Deferred:*
  serializing message history to the DB for restart-safe resumption — not
  needed for Slice 0's ephemeral, single-user sessions.

### 4. Verdict

The agent concludes with a `ConcludeOutput` (`status` ∈
`still_open`/`fixed`/`inconclusive`, plus `rationale`) when confident, or the
orchestrator forces `inconclusive` ("budget exhausted") if the agent exceeds a
step budget (`LiveSession.max_steps`, default 8) without concluding — the
"give up" backstop. The verdict is recorded both on the `retest_sessions` row
(`verdict_status`/`verdict_rationale`) and as a `verdict` transcript event.
**Provenance is the session id + its transcript** — not, yet, a first-class
`Verdict`/`Evidence` row (see Consequences). The verdict is
**agent-determined and human-overridable**: Slice 0 does not yet ship the
override UI (see Deferred, below) — the human's only Slice 0 lever over the
outcome is approve/reject during the run and "End session".

### NFR-02 reproducibility shift (stated plainly)

Today a verdict is a pure function of one request's stored evidence, so
`audit.rederive_run` (ADR-0015) recomputes it **offline, deterministically**,
from the trail alone. Under the agentic model there is no such pure function:
the verdict is a **human-adjudicated agent judgment** produced by an
open-ended sequence of commands and observations. Reproducibility here means
the append-only `session_events` transcript is **replayable and
inspectable** — every command, its rationale, its captured output, every
human approve/reject decision, and the final verdict, in order — **not**
deterministic recomputation. This is a **stronger human-in-the-loop
contribution** (the human is in the loop on every single command, not just a
batch) but a **weaker deterministic-reproducibility claim** than the FR-09
batch model. This trade-off is recorded here explicitly and will be stated
plainly in the thesis (Design + Evaluation chapters), not papered over.

### Supersession

This **supersedes the FR-04/FR-05/FR-07/FR-08/FR-09 batch-plan execution
model over time** — but not yet, and not all at once. Per the spec's build
order, the old path (`plan.py`/`approval.py`/`retest.py`, and their SPA stage
pages) **stays fully operational** through Slices 0–4; Slice 5 is where the
agentic verdict gets wired into FR-09/FR-10/FR-12 and the old path is
retired. Until then the two paths coexist: a finding can be retested either
via the FR-04→FR-09 plan wizard or via the new `/retest-session` console.

## Alternatives considered

- **Two agent tools (`run_command` + `conclude`), as the spec originally
  sketched.** Rejected in favor of one gated tool + a `ConcludeOutput`
  structured output: concluding is a *terminal output*, not another
  suspendable tool call, so there is no need for a second tool with its own
  approval semantics.
- **A blocking background thread per pending approval** (spec §9's named
  fallback). Rejected once Pydantic AI's deferred-tool mechanism proved to
  work in practice: no blocked OS threads, no custom suspension machinery,
  resumption is a first-class framework operation (`deferred_tool_results`).
- **`subprocess` to the `docker` CLI** instead of the `docker` SDK. Rejected:
  stringly-typed, trips ruff's bandit rules, and breaks with the rest of
  `src/` (no existing `subprocess` shell-out).
- **An async pub/sub broker for the WebSocket**, instead of polling
  `session_events`. Rejected for Slice 0: commands run to completion before
  an output event is written, so poll latency is invisible; true
  char-by-char streaming is deferred to Slice 1's shared PTY, which will need
  a different mechanism anyway.
- **Persisting live agent state (message history) to the DB** for
  restart-safety. Deferred: adds real complexity (serializing Pydantic AI
  message history) for a single-user, ephemeral-session model where a
  restart mid-session is an accepted, rare loss — not worth building before
  it's needed.
- **Reshaping `Verdict`/`Evidence` now** to hold a multi-command shell
  conclusion. Rejected for Slice 0: the existing domain model is
  request/response-shaped and a hasty reshape would be premature before the
  chat/plan-panel/free-launch slices settle what a "verdict" needs to
  reference. Deferred to Slice 5, alongside FR-10/FR-12 integration.

## Consequences

- **Easier.** The agent can observe a real response and correct course
  instead of guessing once and failing the whole batch — directly answers the
  false-`fixed`-on-a-bad-SQLi-payload failure mode that motivated this ADR.
  The sandbox's safety is structural (network topology), not a promise the
  model or the app code has to keep. The transcript is a genuinely complete
  record: every command, every human decision, every output, in order.
- **Harder / accepted.** Reproducibility is weaker in the deterministic
  sense (see NFR-02 shift above) — this is a deliberate, documented
  trade-off, not an oversight. Live session state does not survive a process
  restart (Slice 0 accepts this; the transcript does survive). The verdict
  is not yet linked into FR-09's `verdicts` table, so FR-10 audit and FR-12
  export do not yet see agentic-session verdicts (Slice 5 work). Two retest
  paths now coexist in the codebase until Slice 5 retires the old one —
  more surface area, temporarily.
- **Safety.** The sandbox is egress-locked by Docker network topology, proven
  by a live system test (`test_retest_session_system.py`) that a non-lab host
  is unreachable from inside it (FR-17 AC4 / NFR-03). No command runs without
  an explicit human approval (FR-17 AC2) — the deferred-tool mechanism makes
  this structural: the model's `run_command` call literally cannot resolve
  without a `ToolApproved`/`ToolDenied` resume. The single-user threat model
  (ADR-0008) is unchanged: no new auth, app stays bound to `127.0.0.1`.
- **Deferred (explicitly out of Slice 0, tracked for later slices):** human
  terminal input / shared PTY (Slice 1); the plan panel and gated plan updates
  (Slice 2); chat steering (Slice 3); free-launch mode, session controls, and
  budget UI (Slice 4 — Slice 0's budget backstop exists server-side with no
  UI); command *editing* before approval (approve/reject only in Slice 0; the
  human's only refinement lever is reject-with-reason); verdict adjudication
  UI and FR-09/FR-10/FR-12 integration, and retiring the old batch path
  (Slice 5).
- **Follow-up.** FR-17's per-slice acceptance criteria in the SRS will be
  extended as each subsequent slice lands, mirroring how FR-16 accumulated
  its criteria across its PRs.

## Update (2026-07-16): chat-centric layout + revised slice order

Álvaro's design call after Slice 0 shipped: the console should read as **a chat
with the model**, not a terminal with an approval card bolted underneath. This
does not change the architecture this ADR records (sandbox, gated exec, agent
loop, transcript audit, agent-determined verdict) — it is a **presentation
decision** about the console's information architecture, plus a re-ordering of
the unbuilt slices. Recorded here rather than in a new ADR because nothing about
the accepted mechanisms changes.

**Layout (authoritative):** the **center column is the chat** — the agent's
voice: rationale → each gated command as a chat card with **inline
approve/reject** → the verdict, as one scrolling conversation (`agent_message`
events, already in the domain enum, are reserved for future agent prose; the
human's own messages join the same stream from the chat-input slice). The
**terminal docks to the bottom** as a collapsible, read-only panel showing only
*executed*-command output. This supersedes Slice 0's terminal-on-top / card-
beneath arrangement (a purely presentational change to `RetestSession.tsx`; the
`/api` + WebSocket contract is untouched).

**Revised slice order (authoritative — supersedes the numbering used in the
sections above, which predate this update):**

| Slice | Adds |
|------:|------|
| 0 (shipped) | skeleton — read-only terminal + approval card + verdict |
| **1** | **chat-centric console shell** (this decision) — chat center + docked collapsible terminal; presentation only, steering stays approve/reject |
| 2 | shared interactive PTY — human types into the docked terminal; agent observes |
| 3 | plan panel — initial + gated plan updates |
| 4 | chat **input** / steering & Q&A — human messages the agent in the center chat |
| 5 | free-launch mode + session controls + step/wall-clock budget UI |
| 6 | verdict adjudication + FR-09/FR-10/FR-12 integration; retire the old batch path |

The old batch-plan path stays fully operational until **Slice 6** (was Slice 5).
Slice 1 issue: [#90](https://github.com/SelfishCoconut/revalid/issues/90).

## References

- Design spec: `docs/superpowers/specs/2026-07-16-agentic-retest-console-design.md`
- Implementation plan: `docs/superpowers/plans/2026-07-16-agentic-retest-console-slice-0.md`
- Epic: [#87](https://github.com/SelfishCoconut/revalid/issues/87); Slice 0 issue: [#88](https://github.com/SelfishCoconut/revalid/issues/88)
- SRS: FR-17 (`docs/requirements/srs.md`)
- Superseded-over-time: ADR-0011 (retest-plan generation), ADR-0012 (server-side plan approval gate), ADR-0014 (execution sanity checker), ADR-0019 (retest-technique registry) — all stay accepted and operational until Slice 6
