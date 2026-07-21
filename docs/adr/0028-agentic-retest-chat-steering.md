# 0028. Agentic retest chat steering & Q&A (FR-17 Slice 4)

Date: 2026-07-16
Status: accepted
## Context

FR-17 Slice 4 (epic [#87](https://github.com/SelfishCoconut/revalid/issues/87),
issue [#96](https://github.com/SelfishCoconut/revalid/issues/96)) adds the
design spec's third steering channel: **chat**. The operator types free-text
to steer the agent ("focus on the login endpoint") or ask questions ("what
did that 500 mean?"), completing the trio with *approve/edit* (ADR-0025) and
*type a command* (ADR-0026, the `!` path).

The Slice 0 gate constrains delivery. The agent is never idle — it is either
running, suspended on a deferred tool call (the common resting state), or
terminal — and Pydantic AI cannot accept a new user prompt while a tool call
is deferred, nor interrupt a running turn. So an operator message can only be
**buffered and delivered at the next turn boundary**, which while commands are
gated is the next approve/reject.

## Decision

1. **Pure-queue delivery, never an interrupt.** A message is buffered on the
   live session and delivered on the next approve/reject; it never silently
   discards a pending proposal. Redirect = reject-with-message; augment =
   approve-with-message. (The autonomous drain — picking messages up mid-loop
   without a gate — arrives with free-launch, Slice 5.)
2. **Delivered as a first-class user turn.** On the resume, the drained
   message is passed as `user_prompt` alongside `deferred_tool_results`, so it
   lands as a real `UserPromptPart` after the tool return — a model responds
   to a user turn far more reliably than to prose folded into a tool result.
3. **Q&A via a non-gated `respond` tool.** The agent emits prose through
   `respond` (`agent_message` event); being a normal tool, the run continues
   to its next proposal/verdict. No new output type, no new session state,
   budget-exempt.
4. **Observed-fact vs operator-voice split.** `!`-command *results* (Slice 2)
   stay folded into the tool result the agent reads; chat messages are the
   operator's *voice* (a user turn). Two channels, two framings.
5. **No dequeue (audit).** A sent message is committed to the append-only
   transcript immediately (evidence, NFR-02); the UI shows undelivered
   messages with a "queued" treatment but there is no edit/remove.

## Alternatives considered

- **Message implies reject** (auto-reject the pending proposal so a steer
  takes effect in one action) — rejected: silently discards a proposal and
  breaks augment-and-approve.
- **Fold the message into the tool-result string** (like `!` observations) —
  rejected: a model treats a buried tool-result note as data, not an
  instruction; a user turn is read reliably.
- **A new non-terminal prose `output_type`** — rejected: the run would end on
  prose, forcing the orchestrator to re-drive with no user prompt; a `respond`
  tool keeps prose a mid-run side-effect with no loop change.

## Consequences

- **Good:** full three-channel steering; Q&A reuses the already-reserved
  `agent_message` plumbing; zero new session states; no change to the gate,
  budget, or egress lock. Fully testable with a scripted `FunctionModel`
  (message queued → delivered as `user_prompt` → `respond` observed).
- **Accepted limitations:** a message sent while the agent finalizes a
  `conclude` may go unread (still recorded); a question asked while a command
  is pending is answered on resolving it — both inherent to the pure-queue
  gate, stated plainly.
- **Invariants preserved:** command gating, egress lock (NFR-03), and the
  budget backstop are unchanged (`respond` and queued messages are
  budget-exempt by design); the old batch path still coexists until the last
  slice.

## References

- Design spec: `docs/superpowers/specs/2026-07-16-agentic-retest-console-slice-4-design.md` (chat steering + `respond` Q&A tool)
- Plan: `docs/superpowers/plans/2026-07-16-agentic-retest-console-slice-4.md` (Slice 4)
- Builds on ADR-0025 (agentic console), ADR-0026 (operator `!` commands) and ADR-0027 (guiding plan); epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#96](https://github.com/SelfishCoconut/revalid/issues/96)
