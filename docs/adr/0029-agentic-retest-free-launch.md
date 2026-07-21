# 0029. Agentic retest free-launch mode + budgets (FR-17 Slice 5)

Date: 2026-07-17
Status: accepted
## Context

FR-17 Slice 5 (epic [#87](https://github.com/SelfishCoconut/revalid/issues/87),
issue [#100](https://github.com/SelfishCoconut/revalid/issues/100)) adds a
**free-launch** mode — a switch that auto-approves the agent's commands so it
runs the reason→run→observe loop without a click per step — plus a **configurable,
visible budget** and a distinct **give-up** state.

Two situations motivate it. A boring stretch (recon/enumeration can take a dozen
commands before the interesting one) has no safety payoff from per-command
approval — the egress-locked sandbox (ADR-0025) already bounds the blast radius.
And a future FR-15 evaluation must drive the agentic path over a set of findings
without a human in the approval loop.

The step budget already exists (Slice 0: `max_steps`, force-conclude
`inconclusive`) but was a hardcoded `8`, invisible, and unconfigurable. The
design spec also promised a wall-clock budget that Slice 0 never built.

## Decision

1. **Free-launch reuses the gate; it does not fork the execution path.** When the
   orchestrator emits a *command* proposal, a free-launch session immediately
   drives the **same** approval chokepoint a human click would — the same
   compare-and-swap on the pending call, the same step-budget check, the same
   transcript events. There is exactly one place a command runs. The only
   difference is a `{"auto": true}` flag on the `command_approved` event, so the
   audit trail stays honest about which commands a human vetted.
2. **Plan changes stay gated, always.** The auto-approve loop stops on a
   `set_plan` proposal (ADR-0027): commands are cheap and contained, but the
   plan is the agent's stated intent — a change of direction always needs a
   human decision, even in free-launch.
3. **Iterative loop, not recursion.** The free-launch driver (`_drive_auto`)
   loops over successive proposals by calling the resume primitive directly (one
   agent turn per pass), never recursing through the decision entry point, so a
   large `max_steps` cannot blow the stack.
4. **Two budget bounds, one give-up exit.** The step budget applies in both
   modes; a wall-clock budget (`max_seconds`) applies **only in free-launch**,
   checked at step boundaries (the orchestrator holds control only between agent
   turns). Either bound force-concludes `inconclusive` via one shared
   `given_up` path, citing `"budget exhausted"` or `"time budget exhausted"`.
5. **Both entry points.** Free-launch (and the budgets) are settable at session
   start (`POST /retest-session` body — the headless entry an eval run uses) and
   toggleable live (`POST /retest-sessions/{id}/free-launch`); enabling mid-session
   auto-approves any pending command, and every toggle is a `free_launch_changed`
   transcript event.

## Alternatives considered

- **A forked auto-execute path** (a second code path that runs commands without
  the gate) — rejected: two places a command runs means two audit framings and a
  divergence risk; reusing the gate keeps one chokepoint.
- **Recursion through the decision entry point** (auto-approval calls the same
  function a human decision does, which re-drives) — rejected: recursion depth
  grows with the step budget; the iterative loop is O(1) stack.
- **Wall-clock budget in both modes** — rejected: in gated mode the elapsed time
  includes human think-time, so a clock would trip falsely; the bound only makes
  sense while the agent runs autonomously.
- **An explicit "give up" button** — rejected as redundant: the human already has
  **End session**, and the agent gives up via a budget bound; Slice 5 only needs
  to *surface* that state, not add a control.

## Consequences

- **Good:** the operator chooses their oversight level; a headless eval can drive
  the agentic path; the budget backstops are now visible (a meter) and
  configurable. No change to the sandbox, egress lock (NFR-03), or single-user
  threat model (ADR-0008) — free-launch removes the human *pause*, not the
  containment. Fully testable with a scripted `FunctionModel` + `FakeSandbox`
  (auto-run to verdict, plan-still-gated, both budgets → give-up, live toggle).
- **NFR-02 (reproducibility):** every mode change and every auto-approval is a
  transcript event, so a replayed session shows exactly what ran under which
  mode — the transcript stays the complete record (consistent with ADR-0025's
  replayable-transcript reframing).
- **Accepted limitations:** the wall-clock bound is enforced only at step
  boundaries, not mid-command (a hung command is bounded by the existing
  per-command sandbox timeout, not this budget); a free-launch run on a slow
  local model can take minutes, which the meter and the wall-clock bound make
  visible/bounded rather than surprising.
- **Invariants preserved:** command gating (for the human-in-the-loop mode),
  plan gating (always), and the egress lock are unchanged; the old FR-04/05/07-09
  batch path still coexists until Slice 6 retires it.

## References

- Design spec: `docs/superpowers/specs/2026-07-17-agentic-retest-console-slice-5-design.md` (free-launch + budget/give-up)
- Plan: `docs/superpowers/plans/2026-07-17-agentic-retest-console-slice-5.md` (Slice 5)
- Builds on ADR-0025 (agentic console + egress lock), ADR-0027 (guiding plan gate); epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#100](https://github.com/SelfishCoconut/revalid/issues/100)
