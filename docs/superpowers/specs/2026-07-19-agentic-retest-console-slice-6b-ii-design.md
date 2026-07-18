# FR-17 Slice 6b-ii — user-owned goal (FR-04 repurposed) (design)

- **Epic:** #87 · **ADR:** 0032 (proposed) · **Milestone:** M6
- **Depends on:** Slice 6b-i (agentic evidence). **Followed by:** 6b-iii (retire batch execution + finding-flow reshape).
- **Date:** 2026-07-19

## 1. Problem

The agentic console has a *guiding plan* today (Slice 3), but it works backwards
from what the retest UX needs: the **agent** proposes the plan (`set_plan`, gated),
the human approves it. And the agent's actual *goal* is seeded implicitly from the
finding text (`_finding_prompt`). Álvaro's direction reverses this: **the plan is a
user-owned goal.** FR-04 generates it, it is handed to the agent at start, shown as
"Current goal," and the **user alone** edits it (before or during the run); the
agent works to whatever the current goal says. This also resolves "why delete
FR-04" — it is **repurposed**, not removed.

Two constraints from the design session:

- **Generic** — the goal generator must adapt to *any* finding class (XSS, IDOR,
  SSRF, traversal, …), never bake in SQLi/auth-token assumptions.
- **User-owned** — the agent no longer proposes the plan; `set_plan` is removed, so
  there is one source of truth for the goal (the user).

## 2. Decision

### 2.1 Generic goal generation (repurposed FR-04)

A new `generate_goal(finding, *, agent) -> tuple[str, ...]` in `plan.py` (FR-04's
home) asks the LLM for a few **concise, finding-agnostic** verification steps —
"re-exercise the reported condition and observe whether it still occurs," phrased
for the specific finding but with no assumption about vulnerability class or
protocol. Structured output `GeneratedGoal(steps: tuple[str, ...])` (1–6 steps),
reusing the plan-agent build + FR-13 model switch; FunctionModel-testable. This is
*separate from* `generate_plan` (which still emits HTTP `Probe`s for the batch path
until 6b-iii) — a new, tool-agnostic output, not a reshaping of the probe gate.

### 2.2 The goal is the existing plan panel, now user-driven

The Slice-3 "current plan" panel already derives a `string[]` from the latest
`plan_updated` transcript event and survives reload. **Reuse it unchanged** as the
"Current goal" panel — only its label and the *writer* change. The `plan_updated`
event stays the goal's source of truth; the goal is never a separate store.

### 2.3 Seed at session start

`run_first_step` (already a background task) generates the goal via
`generate_goal`, appends an initial `plan_updated` event (so the panel shows it),
and prepends it to the retest agent's prompt:

```
Current goal:
- <step 1>
- <step 2>
...
<the finding context, as today>
```

Goal generation is best-effort: if it fails or yields nothing, the session starts
with an empty goal (the agent falls back to the finding context, exactly as today)
— start never blocks on the goal.

### 2.4 User-owned editing + regenerate

- `POST /api/retest-sessions/{id}/goal {steps: list[str]}` — replaces the goal:
  appends a `plan_updated` event (panel updates) **and** queues the change for the
  agent's next turn. Reuses the message-injection pattern: `LiveSession` gains a
  `pending_goal: list[str] | None`; `_resume_with_decision` drains it and prepends
  `"The operator set the goal to:\n- …"` to the next `user_prompt` (alongside any
  queued chat messages), then clears it. So an edit mid-run reaches the agent on its
  next approve/reject, never interrupting a run (pure-queue, same as chat).
- `POST /api/retest-sessions/{id}/goal/regenerate` — re-runs `generate_goal` and
  applies the result through the same path (event + queued injection).
- A no-op if the session is not live (terminal), like the other steer endpoints.

### 2.5 Remove `set_plan` (the agent no longer proposes the plan)

- **`retest_agent.py`** — remove the gated `set_plan` tool, the `emit_plan` dep +
  `_no_emit_plan`, and the plan bullet from the instructions. The agent keeps
  `run_command` (gated), `respond` (non-gated), and the `ConcludeOutput` verdict.
- **`retest_session.py`** — the deferred-tool gate now only ever carries a
  `run_command`, so the `pending_kind` command/plan split, the `set_plan` branch in
  `_emit_proposal`, the plan mapping in `_decision_event_kind`, the `awaiting_plan`
  transient state, the `emit_plan` wiring in `_make_deps`, and the plan guards in
  `_drive_auto` collapse to the command-only path.
- **`domain.py`** — remove the now-unused `AWAITING_PLAN` status and the
  `PLAN_PROPOSED`/`PLAN_APPROVED`/`PLAN_REJECTED` event kinds. **Keep
  `PLAN_UPDATED`** (the goal's event). The DB is ephemeral (recreated), so dropping
  unused enum values is safe.
- **`app.py`** — the approve/reject endpoint no longer needs to distinguish a plan
  decision; its handler simplifies to the command path.

### 2.6 Frontend

- The panel is relabeled **"Current goal"**; it gains an **Edit** affordance (edit
  the steps as text) and a **Regenerate** button (`generate_goal` again).
- Remove the plan-**proposal** approval card (`plan_proposed` → approve/reject) and
  the `awaiting_plan` handling — the agent no longer proposes plans. Command
  approval cards are unchanged.
- `api/client.ts` gains `setSessionGoal(id, steps)` and `regenerateSessionGoal(id)`.

## 3. Scope boundary

6b-ii does **not** retire the batch *execution* path (FR-05 execute / FR-07 batch
run / FR-08 sanity-on-batch / HTTP probe verdicts) or reshape the finding-detail
wizard — that is 6b-iii. `generate_plan` (probes) stays for the batch path;
`generate_goal` is additive. The pre-start "Goal stage" (editing the goal *before*
the session starts) also lands with 6b-iii's finding-flow reshape; 6b-ii delivers
generation-at-start + **live** editing.

## 4. Acceptance criteria (→ SRS FR-17)

1. Starting an agentic session generates a generic, finding-agnostic goal (a few
   NL steps), shown in the "Current goal" panel and given to the agent; generation
   failure degrades to an empty goal without blocking start.
2. The user (alone) can edit or regenerate the goal live; the change updates the
   panel immediately and reaches the agent on its next turn (pure-queue), never
   interrupting a run.
3. The agent no longer proposes the plan — `set_plan` and its
   `awaiting_plan`/`plan_proposed/approved/rejected` orchestration are gone; the
   command gate + egress lock are unchanged.

## 5. Test plan (pyramid)

- **unit** — `generate_goal` maps a FunctionModel's structured output to steps and
  degrades to empty on no output; `set_goal`/`regenerate_goal` append `plan_updated`
  and queue `pending_goal`; `_resume_with_decision` injects the queued goal into the
  next `user_prompt`; the orchestrator's command path is unaffected by the removal
  (existing command/free-launch/message tests stay green); building the agent
  exposes no `set_plan` tool.
- **integration** — start a session (scripted plan agent + retest agent + FakeSandbox)
  → the initial `plan_updated` carries the generated goal; `POST …/goal` updates the
  panel event and the agent observes it on the next approval; `POST …/goal/regenerate`
  re-seeds; there is no `plan_proposed`/`awaiting_plan` path.
- **frontend** — the panel shows the goal + Edit + Regenerate; editing calls
  `setSessionGoal`; the plan-proposal card is gone.
