# 0040. Guided retest is operator-driven: one action per turn, then hand back

Date: 2026-07-23

Status: accepted
> **Amended by [0042](0042-one-voice-five-states-over-guided-mode.md) and
> [0046](0046-handed-back-console-waits.md):** the guided park is now
> `awaiting_operator` (not `needs_guidance`), and the console renders no prompt there —
> the agent's message is the hand-back, and Conclude is a permanent control. One action
> per turn, and never self-concluding while guided, are unchanged.

## Context

Since the agentic console (ADR-0025), free-launch (ADR-0029) and pause-and-ask
(ADR-0034), the retest agent in **gated mode** (Auto-run OFF) behaves as a
goal-seeker fitted with per-command approval gates. After each approved command,
`_resume_with_decision` resumes the agent, which immediately proposes the *next*
command, and `_dispatch_output` re-opens the approve gate. The operator therefore
faces an **approval treadmill**: the agent is always mid-plan, always pushing the
next proposal, marching to a `fixed`/`still_open` verdict it writes itself.

Live use with Álvaro surfaced the wrong shape for a *supervised* tool. The gate
keeps him in control of **what runs**, but not of the **pace or the direction** —
the agent owns the initiative. When he wants to guide a session (run one thing,
look, think, run another) there is no resting state where the agent has done a
thing and is simply *waiting for him to decide what's next*. `awaiting_command`
is "the agent has a proposal, decide on it"; there is no blank-slate "your move".

The intended contrast is a real-agent one: **"go for it" hands over the wheel**
(this already exists — Auto-run / free-launch); **anything else, the operator
drives** and the agent executes a single step and waits.

## Decision

Make **guided mode (free-launch OFF) do exactly one action per operator turn,
then park** in the existing non-terminal `needs_guidance` state (sandbox alive,
no verdict):

- After an approved command runs, the resume's next output **does not re-open the
  gate and does not terminate** — the session parks. A proposed next command is
  surfaced as an **advisory suggestion** in the pause reason ("ran X; I'd try Y
  next"), *not* held as a pending gate. A `fixed`/`still_open` output is surfaced
  as a **recommendation** for the operator to confirm — **the agent never writes a
  terminal verdict on its own while guided.** Only the operator concludes (ADR-0034
  already made `inconclusive` operator-only; this extends operator-only conclusion
  to all three statuses *in guided mode*).
- **Free-launch (Auto-run ON) is unchanged.** It auto-approves, chains, and drives
  to an agent-authored verdict exactly as today. Flipping Auto-run on *is* "go for
  it"; it stays an explicit toggle (no natural-language trigger — ADR-0034's
  no-classifier stance holds).
- **The per-command approve gate stays.** An operator instruction still flows
  `propose → approve → run → park`; a literal one-touch command remains the `!`
  path (ungated, does not wake the agent — ADR-0026).
- **The agent's instructions branch on mode** (dynamic on `deps.free_launch`):
  the guided persona is "do what the operator asked, report briefly, and wait —
  the operator decides the next step and makes the final call"; the autonomous
  persona keeps today's drive-to-a-verdict wording.

## Alternatives considered

- **Keep the gate as the only control (status quo).** Rejected: the gate governs
  *what* runs, not *initiative*; the operator still never gets a blank-slate
  "your move" state, which is the actual complaint.
- **Resume and report, but keep chained proposals as held gates.** Rejected: a
  pending proposal sitting at the pause reintroduces the "agent is pushing the
  next step" feel and breaks `continue_session`'s "never pending at a pause"
  invariant (ADR-0034). The suggestion is advisory text instead.
- **Let the agent self-conclude in guided mode when confident** (the second
  option offered). Rejected by Álvaro: guided means the operator always makes the
  final call; a verdict is a recommendation until he confirms.
- **A natural-language "go for it" that flips Auto-run.** Rejected: needs an
  intent classifier the codebase deliberately avoids; a toggle is unambiguous and
  cannot misread a message as "race to the end".

## Consequences

- **The operator can actually drive:** run one thing, look, think, steer via chat,
  or hand off with Auto-run — matching the single-supervising-user model
  (ADR-0008). This is the "less brainless goal-seeker" the tool was missing.
- **`needs_guidance` becomes the normal resting state of a guided session,** not
  only an exhausted-options hand-back. Its reason text now also carries the
  routine "ran X — your move" / suggested-next-step / verdict recommendation. The
  SPA already renders and resumes this state (pause banner: Keep going / Conclude);
  copy likely needs a lighter, non-alarming framing for the routine case.
- **In guided mode the only terminal path is the operator concluding.** An
  agent-authored verdict is reachable only under Auto-run. This further tightens
  FR-09 verdict provenance (fewer, more deliberate verdicts).
- **Implementation risk to settle in the plan:** parking cleanly after a resume
  that produced a `DeferredToolRequests` (an unresolved approval-required tool
  call left in the message history) needs care with Pydantic AI's deferred-tool
  semantics — resolve-and-discard vs. re-run fresh on continue. The existing
  `stopped` / `needs_guidance` handling is the reference.
- **Unchanged:** free-launch, the egress lock (NFR-03), the approve gate itself,
  and the `!` manual-command path.

## References

- Enhances FR-17 (agentic retest). Builds on ADR-0025 (console), ADR-0026 (`!`
  commands), ADR-0028 (chat steering + `respond`), ADR-0029 (free-launch),
  ADR-0034 (pause-and-ask) and ADR-0035 (cockpit redesign), under ADR-0008
  (single supervising user). Issue #201.
