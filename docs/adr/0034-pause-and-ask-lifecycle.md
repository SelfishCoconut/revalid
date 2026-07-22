# 0034. Pause-and-ask retest lifecycle — no give-up (FR-17 Slice 8)

Date: 2026-07-19

Status: accepted
> **Amended by [0035](0035-remove-retest-step-budget.md):** pause trigger 1 (the
> step budget) is removed. The only remaining trigger is trigger 2 — the agent
> handing back after exhausting its options. `continue` no longer raises a budget;
> it just resumes.

## Context

Since ADR-0025 the agentic retest session has had a **give-up** backstop: a step
budget (approved commands) *or* a wall-clock budget (free-launch only) would, when
exhausted, force-conclude the session `inconclusive`, mark it `given_up`, and tear
down the sandbox (`record_verdict` + `_mark_given_up` + `_teardown`). The agent
could also self-conclude `inconclusive` at will (ADR-0030 auto-persists whatever it
concludes as a queryable verdict).

Live use with Álvaro surfaced that this is the wrong shape for a supervised tool:

- A budget or a stuck agent produces a **spurious `inconclusive` verdict** and
  throws away a live sandbox the operator was still working in — the exact moment
  they most want to step in, take over the terminal, or redirect the agent.
- A **wall-clock** give-up is meaningless here: the operator is present and gating
  each command; think-time isn't the agent's fault, and a timed quit just discards
  work.
- `inconclusive` is a **human** judgement ("I looked and can't tell"), not
  something the agent should be able to stamp on a finding unattended.

Álvaro's decision: the agent should stop only on a *real* determination
(`fixed`/`still_open`); anything else — a step budget reached, or the agent having
**exhausted its known options** — should **pause and ask the operator**, keeping the
sandbox alive, rather than quit.

## Decision

Replace the give-up backstop with a **pause-and-ask** lifecycle.

**New non-terminal state `needs_guidance`.** A paused session keeps its
`LiveSession` (sandbox alive, registry entry retained); no verdict is written. The
`STATE_CHANGE` to `needs_guidance` plus a `needs_guidance` transcript event carry a
human-readable **reason**.

**Two pause triggers, funnelled through `_pause_for_guidance`:**

1. **Step budget reached.** The budget still counts approved commands, but instead
   of refusing the over-budget command it is *held*: when the agent proposes a
   command and `step_count >= max_steps`, the session pauses with the command
   pending rather than surfacing an approve gate. There is **no wall-clock budget**
   — `max_seconds` and its `clock`/`started_at` plumbing are deleted.
2. **Agent concludes `inconclusive`.** Reinterpreted, not persisted: the agent
   handing back `inconclusive` *is* "I've exhausted my options — guide me." It
   pauses with the agent's rationale as the reason. The agent can therefore no
   longer write a terminal `inconclusive` verdict; the surface (`ConcludeOutput`)
   is unchanged, so the deferred-approval output-tool wiring and the test scripts'
   single-output-tool assumption are untouched — only the orchestrator's
   interpretation changes.

**Two operator exits from a pause:**

- **Keep going** (`POST …/continue {extra_steps?}`) raises `max_steps` by the
  increment and resumes: a held command re-opens its approve gate (or auto-runs in
  free-launch); an exhausted-options pause re-runs the agent, folding in any queued
  goal/chat guidance. Repeatable until the next pause or a real conclusion.
- **Conclude** (`POST …/conclude {status, rationale}`) writes the operator's verdict
  (`actor="operator"`, any of the three statuses — this is the only path that can
  produce `inconclusive`) and tears down.

`given_up` is retired as a produced state (kept in the enum + `_TERMINAL` only so
legacy rows stay terminal). `inconclusive` becomes operator-only.

## Consequences

- **The operator is always in the loop at the boundary.** A stuck agent or a spent
  budget becomes a question with the sandbox intact, not a discarded run — matching
  the single-supervising-user model (ADR-0008).
- **No unattended `inconclusive`.** A headless free-launch run that can't decide now
  *pauses* rather than stamping a verdict; it reaches the `verdicts` table only via
  a real agent conclusion or an operator conclude. This tightens FR-09 outcome
  quality (fewer, more meaningful verdicts) and is the counterpart to the
  home-ledger "one determination per finding" change (later slice).
- **A session can now sit non-terminal indefinitely.** The WS tail and SPA poll no
  longer assume every session marches to a terminal state on its own; they already
  key off the latest `state_change`, so a `needs_guidance` session streams and
  resumes across reloads like any live one. The operator (or End) is the terminator.
- **Give-up code is deleted, not soft-flagged** (dead code is the project's #1
  pathology, CLAUDE.md): `_give_up`, `_mark_given_up`, `_step_budget_exhausted`,
  `_time_budget_exhausted`, and the `max_seconds`/`clock`/`started_at` plumbing all
  go. The `max_seconds` DB column is dropped from the model (existing rows keep the
  now-ignored column harmlessly).
- **FR-06 / NFR-03 unchanged:** egress is still the sandbox's Docker `--internal`
  network; a paused session's still-alive sandbox is as locked-down as a running
  one.

Supersedes the give-up half of **ADR-0029** (free-launch + budget backstop); the
free-launch auto-approve loop itself is unchanged apart from dropping the time
check and respecting the pause.

## Update — 2026-07-21: full operator lifecycle controls (issue #150)

Live use surfaced that the operator's control over a session was too thin: they
could only conclude at a `needs_guidance` pause, and Restart auto-ran the fresh
attempt. Álvaro's decision — give the operator the full lifecycle, each control
surfaced only when it makes sense:

- **Conclude at any live point**, not just at a pause. `conclude_session` already
  guarded only on terminality; the gap was the UI. The backend was hardened for
  the mid-flight case: concluding while an agent step is in flight tears the
  sandbox down, which makes the in-flight `run_command` raise — `_fail` now
  swallows that failure when the row is already terminal, so the operator's
  verdict is never clobbered by a late `error`.
- **Stop / Resume** — a new non-terminal `stopped` state. Stop is a *cooperative*
  pause: a command already running finishes and is recorded, then the session
  parks with its sandbox kept alive; Resume re-opens a held command's gate or
  re-runs the agent. The free-launch loop halts while stopped.
- **Deferred Restart** — a new non-terminal `idle` state. Restart opens the fresh
  session `idle` (goal + scope recorded, no sandbox) so it never auto-runs; the
  operator presses **Start** to provision and begin. Initial launch is unchanged
  (immediate).

New endpoints: `POST /retest-sessions/{id}/{start,stop,resume}`; launch gains a
`deferred` flag. The egress lock and the per-command gate are unchanged — these
are lifecycle controls layered on top, not a relaxation of containment.

## Update — 2026-07-22: the chat resumes the agent (issue #163)

The lifecycle above gave the operator three separate buttons for one idea —
"carry on": **Wake the agent** (`idle`), **Resume** (`stopped`), **Keep going**
(`needs_guidance`). Driving it live, Álvaro's call: the console should read like
Claude — you *talk* to the agent to start or continue it.

**Decision: a message to a parked session wakes it, with that message as the
steer.** `POST …/message` dispatches on the session's state:

| state | effect of a message |
| --- | --- |
| `idle` | provision the sandbox and run the first turn, message folded into the opening prompt |
| `stopped` | `resume_session` — re-open a held gate, or re-run the agent |
| `needs_guidance` | `continue_session` — re-run the agent with the reply as its user turn |
| anything else (mid-run, or `awaiting_command`) | queue for the next turn boundary, as before |

**No intent classifier.** Whether a message is a question or an instruction is
decided by the *agent's own tool choice*, which the turn pays for anyway: the
ungated `respond` tool answers, the approval-gated `run_command` acts. Asking a
woken agent "what did you already try?" gets an answer and it stays put. A
classifier would add an LLM round-trip to guess at something the next turn
decides correctly, and would fail *silently* — a misread instruction leaves the
console looking hung with no signal why.

This is safe precisely because of the gate this ADR never touched: **waking the
agent cannot execute anything.** The worst case is a proposed command sitting at
the approve gate. The one exception is free-launch/Auto-run, which is an explicit
operator handover of that gate.

**The Q&A stand-in is suppressed when the message wakes the agent.** `build_qa_agent`
exists so a question during a *busy* turn is not left hanging; once the real agent
is running it answers itself, with the full message history the Q&A only sees as a
flattened summary. Two voices answering one message is noise.

**Waking is scoped to parked states.** A message at `awaiting_command` must *not*
re-drive the agent — the pending command would be lost — so "busy" deliberately
includes the gate.

Consequences:

- **Resume, Wake the agent and Keep going are deleted from the console.** Stop and
  Restart remain: halting and abandoning stay explicit buttons, while continuing is
  the conversation. One direction is a control, the other is speech.
- The composer is enabled in every non-terminal state, including `idle` — messaging
  is what provisions the sandbox, so gating it on a live sandbox was backwards.
- `POST …/{start,resume,continue}` survive as **programmatic API** (and are still
  what the wake path calls internally); they simply have no button behind them.
- `_start_idle` is split out of `run_start` so the wake path can start an idle
  session on an already-open DB session rather than nesting a second one.
