# 0032. The guiding plan becomes a user-owned goal (FR-17 Slice 6b-ii)

Date: 2026-07-19
Status: accepted
## Context

The agentic console has a *guiding plan* (Slice 3, ADR-0027), but its ownership is
backwards for the retest UX Álvaro wants: the **agent** proposes the plan
(`set_plan`, gated) and the human approves it, while the agent's actual goal is
seeded implicitly from the finding text. The direction is to reverse this — **the
plan is a user-owned goal**: FR-04 generates it, it is handed to the agent at
start, shown as "Current goal," and the **user alone** edits it (before or during
the run); the agent works to whatever the current goal says. This also resolves
"why delete FR-04" from the Slice 6 reframe — it is **repurposed**, not removed.

Two constraints from the design session: the goal generator must be **generic**
(adapt to any finding class, no SQLi/auth-token assumptions), and the goal must be
**user-owned** (the agent no longer proposes it).

## Decision

1. **Generic goal generation (repurposed FR-04).** A new
   `generate_goal(finding) -> steps` asks the LLM for a few concise,
   finding-agnostic verification steps. Separate from `generate_plan` (which still
   emits HTTP `Probe`s for the batch path until 6b-iii) — a new, tool-agnostic
   output, not a reshaping of the probe gate.
2. **Reuse the existing plan panel.** The Slice-3 panel already derives a step list
   from the latest `plan_updated` transcript event and survives reload; it becomes
   the "Current goal" surface, only its label and its *writer* change (the user,
   not the agent). `PLAN_UPDATED` stays the goal's source of truth.
3. **Seed at start, best-effort.** `run_first_step` generates the goal in its
   background task, appends an initial `plan_updated`, and prepends it to the agent
   prompt; generation failure degrades to an empty goal — start never blocks.
4. **User-owned editing, pure-queue.** `POST /retest-sessions/{id}/goal` (+
   `/regenerate`) updates the panel event and reaches the agent on its *next* turn
   (the same queue mechanism as chat messages), never interrupting a run.
5. **Remove `set_plan`.** The agent stops proposing the plan: the gated `set_plan`
   tool, the orchestrator's `pending_kind` command/plan split, the `awaiting_plan`
   transient, and the `plan_proposed`/`plan_approved`/`plan_rejected` events are
   removed; the SPA plan-proposal card goes. `PLAN_UPDATED` + the panel remain.

**Implementation split.** 6b-ii-a implements the **teardown** (decision 5) — a
self-contained simplification that clears the plan-proposal path. 6b-ii-b builds
the **user-owned goal** (decisions 1–4) on the cleaned-up orchestrator.

## Alternatives considered

- **Keep the agent-proposed plan (Slice 3) and add user editing alongside** —
  rejected: two writers of the goal (agent via `set_plan`, user via the endpoint)
  both writing `plan_updated` means last-writer-wins races and no single source of
  truth; "the user owns the goal" requires removing `set_plan`.
- **A single natural-language goal string** instead of steps — rejected: the panel
  and transcript already model a step list; steps read as a checklist the agent and
  human both track, and match "test login on endpoint X"-style phrasing.
- **Generate the goal synchronously in the start request** — rejected: it adds an
  LLM call to the `202` path; generating in the existing background task keeps start
  responsive and degrades cleanly on failure.
- **Render FR-04's HTTP probes as the goal** — rejected: the agent runs arbitrary
  tools, so an HTTP-probe goal reads wrong and stays coupled to the batch `Probe`
  shape 6b-iii retires; a generic NL goal generator is the right repurpose.

## Consequences

- **Good:** the plan becomes a first-class, user-owned instrument; FR-04 is
  repurposed (not deleted); the agentic console reads as "here's the goal, the agent
  works to it, you steer it," with one source of truth.
- **NFR-02 (reproducibility):** every goal change is a `plan_updated` transcript
  event, so a replayed session shows exactly what the goal was at each step —
  consistent with ADR-0025's replayable-transcript reframing.
- **Simplification:** removing `set_plan` collapses the orchestrator's dual
  command/plan gate to a single command path (`pending_kind`, `awaiting_plan`, and
  the plan-approval events are gone) — less state, one gate.
- **Accepted limitation:** editing the goal *before* the session starts (a "Goal
  stage") rides with 6b-iii's finding-flow reshape; 6b-ii delivers
  generation-at-start + live editing. Command gating + the egress lock (NFR-03) are
  unchanged.

## References

- Design spec: `docs/superpowers/specs/2026-07-19-agentic-retest-console-slice-6b-ii-design.md`
- Plans: `docs/superpowers/plans/2026-07-19-agentic-retest-console-slice-6b-ii-a.md` (teardown) + the 6b-ii-b plan (goal)
- Supersedes ADR-0027 (agent-proposed guiding plan). Builds on ADR-0025; epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#107](https://github.com/SelfishCoconut/revalid/issues/107).
