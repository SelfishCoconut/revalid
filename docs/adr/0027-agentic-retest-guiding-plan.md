# 0027. Agentic retest guiding plan — a gated `set_plan` tool

Date: 2026-07-16
Status: proposed

## Context

FR-17 Slice 3 (epic [#87](https://github.com/SelfishCoconut/revalid/issues/87),
issue [#94](https://github.com/SelfishCoconut/revalid/issues/94)) adds the
design spec's **guiding plan**: "a plan the agent proposes and maintains — every
plan change is human-approved, always." It is a short, human-readable strategy
the agent states up front and revises as it learns — a **transparency + steering
artifact**, distinct from the old FR-04 typed `RetestPlan` (that is the batch
path's *executable* probe list). Execution still happens only through the gated
`run_command`; the guiding plan documents intent and lets the human approve the
*direction*, not just individual commands.

Forces:

- **Reuse the existing gate.** Slice 0 already has a deferred-tool approval
  mechanism (`run_command` → `DeferredToolRequests` → approve/reject by
  `tool_call_id`). "Every plan change is human-approved" should ride that same
  mechanism rather than invent a parallel one.
- **Keep the plan simple.** A walking skeleton needs a plan that is useful but
  cheap to reason about and render.
- **Don't distort the budget.** The step-budget backstop (Slice 0) bounds an
  always-proposing agent by counting *commands*. Plan changes run no command and
  must not consume it.

## Decision

- **Plan = an ordered list of short step strings.** Whole-list replace on each
  change (per-step status is a deferrable nicety).
- **A second gated tool `set_plan(steps, rationale)`** on the retest agent
  (`requires_approval=True`, same as `run_command`). The system prompt tells the
  agent to propose a plan **first**, then work commands, and to revise via
  `set_plan` whenever its strategy changes. On approval the tool runs and records
  the plan; on rejection the agent adapts (the denial reason is surfaced to it).
- **Orchestrator branches on the proposed tool** (`_emit_proposal`): `run_command`
  → `command_proposed` / `awaiting_command`; `set_plan` → `plan_proposed` /
  `awaiting_plan`. `LiveSession.pending_kind` tags the pending approval so
  `apply_decision` records the matching event (`plan_approved`/`plan_rejected` vs
  `command_approved`/`command_rejected`) and so **only command approvals count
  against the step budget** — a plan approval resumes in a transient `thinking`
  state, runs no command. The approved plan is emitted as a `plan_updated` event
  (the current plan; the UI derives it from the latest such event, symmetric with
  how the verdict is derived).
- **No new REST surface.** The plan reuses the existing approve/reject endpoints
  — the gate resolves a pending `tool_call_id` regardless of whether it is a
  command or a plan.
- **Frontend:** a Plan panel at the top of the console shows the current steps;
  plan *proposals* render as approval cards in the chat (steps + rationale +
  inline approve/reject), reusing the command approval controls.

## Consequences

- **Good:** the "every plan change approved" invariant is structural (it reuses
  the deferred-tool gate — a plan can't take effect without a `ToolApproved`
  resume); no new endpoint, no new persistence table (the transcript is the
  source of truth, so the current plan survives reload); fully testable with a
  scripted `FunctionModel` (plan → command → conclude). 371 backend @ 99%,
  107 frontend.
- **Accepted limitations:** whole-list replace (no per-step progress/status yet);
  the plan is advisory (it does not constrain what commands the agent may
  propose — the human still approves each command); a real LLM must be prompted
  to actually propose a plan first (tests script it deterministically).
- **Invariants preserved:** command gating, egress lock (NFR-03), and the
  budget backstop are unchanged (plan approvals are budget-exempt by design); the
  old batch path still coexists until the last slice.

## References

- Design spec: `docs/superpowers/specs/2026-07-16-agentic-retest-console-design.md` (§2 "a guiding plan the agent proposes and maintains")
- Plan: `docs/superpowers/plans/2026-07-16-agentic-retest-console-slice-2.md` (Slice 2); Slice 3 built directly on the spec
- Builds on ADR-0025 (agentic console) and ADR-0026 (operator `!` commands); epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#94](https://github.com/SelfishCoconut/revalid/issues/94)
