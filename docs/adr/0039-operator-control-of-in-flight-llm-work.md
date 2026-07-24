# 0039. Operator control of in-flight LLM work: conversational hand-back, turn restart (unstick), and cancellable extraction

Date: 2026-07-23
Status: accepted

## Context

Live testing of the FR-17 agentic retest console (ADR-0025/0034/0035) and the
FR-01/FR-03 extraction path surfaced four gaps where the operator has no control
over LLM work already in flight:

1. **The agent ignores conversational messages.** When the operator sends a plain
   message — a greeting, an acknowledgement, a quick question — the agent treats
   the goal as a script and bolts for it, proposing a command instead of just
   replying. The output union `[ConcludeOutput, DeferredToolRequests]` gives a turn
   only two ways to end (propose a gated command, or conclude), so there is no way
   for the agent to *reply and wait*: every turn must act.

2. **A wedged model turn cannot be unstuck.** A turn runs as a single blocking
   `run_agent_step` (`asyncio.run` on a worker thread). `Stop` (ADR issue #150) is
   cooperative — it parks *after* the in-flight turn finishes — so if the model
   backend hangs (a slow/frozen local Ollama), the operator can only end the whole
   session. There is no "abandon this turn and try again."

3. **Extraction cannot be stopped.** `run_extraction` runs one model call per
   finding candidate as a fire-and-forget background task with no cancellation, so
   a mis-uploaded or slow report must run to completion.

4. **Deleting a report leaves work running.** `_cascade_delete_report` removes DB
   rows but does not touch the process-local registries, so a live retest session's
   agent + sandbox, or an in-flight extraction, keeps running for a report that is
   gone.

Álvaro made the two load-bearing calls: **restart = abort the current turn and
re-run it** (keep the session, sandbox, goal, history — not a fresh session), and
**a stopped extraction is marked `cancelled` keeping its partial findings** (not
discarded).

## Decision

We will give the operator direct control of in-flight LLM work, on both the retest
and extraction paths.

**Conversational hand-back (`AwaitOperator`).** Add a third agent output type,
`AwaitOperator(message)`, to the retest agent's union. The instructions tell the
agent that the operator's message is its priority and the goal is background
context: a conversational message gets a short `AwaitOperator` reply and a hand-back
— no command, no verdict. The orchestrator surfaces the reply as a normal
`agent_message` and parks the session in a new non-terminal `awaiting_operator`
state, sandbox kept alive. It is deliberately *lighter* than `needs_guidance`
(ADR-0034): no "needs your guidance" banner, just the reply and an open composer.
The operator's next message resumes it, reusing the `continue_session` path.

**Turn restart / unstick.** `run_agent_step` builds its event loop by hand (instead
of `asyncio.run`) and registers the loop + task on the `LiveSession`, so another
thread can cancel a wedged turn cross-thread. `POST …/restart-model` cancels the
in-flight turn and re-runs it from the top (a cancelled turn never committed events
or updated the message history, so the same prompt + history reproduces it). A
cancel that was *not* an unstick (teardown) surfaces as a private `_TurnAbortedError`
that the orchestration boundary routes to `_fail`, which no-ops on the
already-terminal session. `end_session` now also cancels a wedged turn so its thread
never lingers on a hung call.

**"Queued" hint honesty (`messages_delivered`).** Each turn that drains queued
operator messages emits a `messages_delivered` transcript marker, so the console
clears the "queued" hint the moment the agent actually reads a message — on any
resume path, not only approve/reject.

**Cancellable extraction.** A process-local `ExtractionRegistry` holds per-report
cancel flags (with a reason) *and* the event loop + task of the in-flight
extraction. Extraction runs `await agent.run` per candidate on a cancellable loop
(`_run_cancellable_extraction`), so `request_cancel` both flags the report and
cancels the task cross-thread — interrupting the in-flight model call immediately,
not just between candidates. This is the #206 fix: a flag-only cooperative cancel
never took effect when a single candidate's call wedged (a slow/hung local model).
`POST …/reports/{id}/cancel` flags an `operator` stop → the report settles to a new
`cancelled` status keeping whatever was extracted. Deleting a report flags
`deleted` → the worker persists nothing into the row being removed.

**Delete tears down live work.** `delete_report` first flags the extraction
(`deleted`) and ends every live retest session under the report (cancelling any
wedged turn + tearing the sandbox down), then cascades the rows.

## Alternatives considered

- **Reuse `needs_guidance` for the conversational reply.** Rejected: its banner
  ("Paused — needs your guidance") is wrong for a "hi", and the reason-carrying
  `needs_guidance` event would show the reply as a guidance request. A distinct
  `awaiting_operator` state keeps the copy honest.
- **Restart = a fresh session** (the existing `Restart` button). Rejected by Álvaro:
  "unstick" must not throw away the conversation, sandbox, or goal — only the frozen
  turn.
- **Kill the worker thread to unstick.** Rejected: Python threads can't be safely
  force-killed; holding the loop + task handle and cancelling the asyncio task is the
  only clean interruption (httpx cancels the in-flight request).
- **Discard a cancelled extraction / reuse `failed`.** Rejected by Álvaro: keep the
  partial findings under a distinct `cancelled` status so operator-stop reads
  differently from a real extraction error and the partial result stays usable.

## Consequences

- **Easier:** the operator can chat with the agent without it running off; can
  unstick a frozen turn without losing the session; can stop a runaway extraction and
  keep what it found; deleting a report no longer orphans a running agent or sandbox.
- **New surface:** one agent output type (`AwaitOperator`), one session status
  (`awaiting_operator`), one report status (`cancelled`), two transcript event kinds
  (`messages_delivered`, `turn_restarted`), a process-local `ExtractionRegistry`, and
  two routes (`…/restart-model`, `…/reports/{id}/cancel`).
- **Accepted debt / edge cases:** an unstick re-runs the *whole* turn, so a
  non-gated `respond` emitted before the model wedged is re-emitted (a duplicate
  chat line) — acceptable for an emergency action. Both cancellation paths
  interrupt the in-flight model call by cancelling its asyncio task cross-thread
  (httpx cancels the request); the extraction also polls its flag between
  candidates for the graceful case. The registries are process-local (not
  restart-safe), consistent with the existing `SessionRegistry` (ADR-0025).
- Both registries and the cancel/retry live entirely in the app/orchestrator layer;
  the transcript stays the sole audit record (no delta or in-memory state is
  persisted), preserving the FR-10 audit invariant.
