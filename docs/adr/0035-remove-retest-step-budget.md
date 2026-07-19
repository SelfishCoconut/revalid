# 0035. Remove the retest step budget; pause only on agent hand-back

Date: 2026-07-19

Status: accepted

Amends [0034](0034-pause-and-ask-lifecycle.md) (removes its first pause trigger).
Supersedes the configurable step budget added in FR-17 Slice 9 (#122).

## Context

ADR-0034 gave the agentic retest two ways to pause for operator guidance
(`needs_guidance`): (1) a **step budget** of approved commands was reached, or
(2) the agent **handed back** an `inconclusive` conclusion. FR-17 Slice 9 (#122)
then made that budget configurable in Settings, with a "No-limit" option, and
surfaced a `used / max steps` meter in the cockpit.

Live testing with Álvaro found the budget to be the wrong shape for a supervised,
single-user tool:

- The `0 / 8 steps` meter reads like a **broken progress bar** — it never appears
  to move, and "steps" is developer jargon that means nothing to the operator.
- It is a **knob without a purpose here**: the operator is present and gating every
  command, so a hard cap that pauses a working session only interrupts them at an
  arbitrary count. The one real safety concern — a runaway agent — is already
  covered: the operator can End or Restart at any time, and free-launch is opt-in.
- The budget is **redundant as a pause mechanism**: the agent already hands back on
  its own when it exhausts the options it can think of (trigger 2). That is the
  only pause the operator actually wants.

Separately, the cockpit's first-turn state showed *"The agent is preparing its
first step…"* indefinitely. Local models are slow (a first tool-calling turn on
`qwen3:14b` measured ~80 s; the `qwen3.6:27b` default is far worse — 23 GB running
mostly on CPU), so a live turn read as **frozen**. The `RetestSessionStatus.THINKING`
enum value existed but was never emitted.

## Decision

**Remove the step budget entirely.**

- Backend: delete `RetestSessionRecord.max_steps`, `Settings.default_max_steps`,
  `LiveSession.step_count`/`max_steps`, the budget gate in `_dispatch_output`, and
  `extra_steps` from `continue`. `needs_guidance` now has a **single trigger**: the
  agent handing back `inconclusive` (ADR-0034 trigger 2). `continue_session` just
  clears the pause and re-runs the agent — its former "held command" branch was
  dead code (a session only ever pauses via hand-back, never with a command
  pending) and is deleted.
- Frontend: delete the step-budget meter, the Settings budget control, and the
  `max_steps` / `default_max_steps` types and helpers.

**Emit the `thinking` status.** `start_and_step`, `_resume_with_decision`, and
`_resume_run` set `THINKING` before each `agent.run_sync`, so the console shows a
live "thinking…" indicator for the whole (possibly long) LLM call instead of a
static "preparing" line. `RUNNING_COMMAND` is retained in the enum for legacy
transcripts but is no longer emitted.

**Redesign the cockpit** (same `/api` + WebSocket contract): the user-owned goal
moves from a right-hand aside to a full-width panel directly below the stages bar;
the conversation becomes a boxed, chat-like log with the thinking indicator;
"Regenerate goal" gains a loading state; and developer-facing labels
("Agentic retest session", the budget meter, `AUTO` chip, "egress-locked sandbox",
"Free-launch" → "Auto-run") are replaced with plain operator-facing copy.

## Alternatives considered

- **Keep the budget, fix only the display.** Rejected: it preserves the concept the
  operator explicitly does not want, and still adds a knob and a pause trigger that
  serve no purpose in a supervised session.
- **Hide the UI but keep a hidden internal cap.** Rejected: it keeps all the
  `max_steps` plumbing alive as a dead, untunable safety net. A never-concluding
  agent is already bounded in practice by the operator (End/Restart) and by
  free-launch being opt-in, so the cap earns nothing for its complexity.

## Consequences

- **Simpler model, one pause trigger.** The lifecycle is easier to reason about and
  to explain in the thesis: the agent runs until it reaches a real determination or
  hands back; the operator steers, keeps going, or concludes.
- **A never-concluding agent in free-launch could loop indefinitely.** Accepted
  under the single-user, supervised threat model (ADR-0008): the operator watches
  the run and ends it; free-launch is a deliberate opt-in, not the default.
- **Reverses FR-17 Slice 9.** The configurable budget + "No-limit" Settings control
  is removed; its ADR-0034 trigger-1 plumbing (`max_seconds` was already gone) is
  deleted. Local `revalid.db` files predating this change keep vestigial
  `max_steps` / `default_max_steps` columns, which the new models simply ignore
  (no migration framework — ADR-0008).
- **Slow local turns now read as progress**, not a hang, closing the "stuck at
  preparing its first step" report.
