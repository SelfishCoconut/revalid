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
