# 0046. A handed-back console waits: no hand-back prompt, a permanent Conclude, and only a verdict is proposed

Date: 2026-07-25
Status: accepted

## Context

ADR-0042 folded `needs_guidance` into `awaiting_operator` and, in doing so, kept
"a light *your move — reply or record the verdict*" prompt as the way a hand-back
stayed discoverable. In the console that landed as a banner below the thread:

> **The agent handed back — your move**
> Reply below to keep it going — your message is the steer — or conclude to record
> the verdict now.

Live use (Álvaro, 2026-07-25) rejected it. A handed-back session is *the operator
thinking* — reading the output, deciding what to try, or walking away for an hour.
A banner that restates the two things they could do is noise on every single turn
of a guided retest, and it frames the operator's own pace as a pending decision.

The banner was also load-bearing for the wrong reason: it carried the only
**Conclude** button available in `awaiting_operator`, because the toolbar's copy was
deliberately hidden there to "keep a single entry point". So the *state* gated
access to the one control that ends the retest.

The same nagging existed in the agent's voice. `_suggestion_reason` — the guided
one-action report — appended *"Tell me to go ahead, point me elsewhere, or conclude
the retest"* to **every** hand-back, including the many where the agent has reached
no determination at all. Offering to end the retest is not a neutral menu item: it
is a suggestion about the verdict, made by a party that (ADR-0040) is explicitly
not allowed to record one.

## Decision

A hand-back is a *resting state*, not a question. Rebalance who says what.

- **`awaiting_operator` renders nothing of its own.** The agent's message — a
  reply, a guided "ran X — I'd try Y next" report, a verdict recommendation, or
  "I'm out of options" — is the last bubble in the thread, and the console waits.
  The state stays legible from the status line, whose label becomes the factual
  **"Waiting for you"** rather than the imperative "Your move".

- **Conclude is a permanent control**, present in the toolbar in every live state
  (`working`, `awaiting_command`, `awaiting_operator`, `stopped`) and hidden only
  while its own form is open. The operator ends the retest when *they* decide it is
  over; no state grants or withholds that, and no banner has to offer it.

- **The agent proposes concluding only when it has a determination to propose.**
  `_recommendation_reason` (the guided `fixed`/`still_open` recommendation) keeps
  its single "Conclude to record that, if you agree." `_suggestion_reason` loses
  its options menu entirely and just reports. Nothing else in the agent's voice
  mentions concluding.

- **A withdrawn verdict stops being reported.** With the banner gone, the thread's
  verdict box is reachable in `awaiting_operator`, which exposed a latent
  ADR-0043 bug: reopen keeps the `verdict` event in the append-only transcript, so
  the console kept rendering a determination the operator had retracted. The
  `useRetestSession` hook now takes the **later** of `verdict` /
  `verdict_cancelled`, so a reopened session reports no current verdict while both
  events stay in the audit trail.

This **amends ADR-0042** (whose "light prompt" consequence is retired) and
ADR-0034/0040 (the pause-and-ask lifecycle and the guided park are unchanged in
*behaviour* — only their console framing and hand-back copy change), and **fixes**
a reporting bug in ADR-0043.

## Alternatives considered

- **Keep the banner but soften the copy.** Rejected: any persistent element in the
  resting state is the same interruption, and the honest content of that element is
  "nothing is happening because it is your turn" — which the status line already
  says in two words.

- **Keep the banner as the only Conclude entry point, and make it dismissible.**
  Rejected: it keeps a lifecycle control behind a state-specific, dismissible
  affordance. Conclude belongs with Stop / Restart / End session, permanently.

- **Let the agent keep offering "or conclude" at every hand-back.** Rejected: the
  agent may recommend a determination it has actually reached (ADR-0040), but a
  standing invitation to end the retest at every turn is verdict pressure from the
  party the design forbids from recording one.

- **Drop the guided verdict *recommendation* too, for symmetry.** Rejected: the
  recommendation is the useful half — it is the agent's opinion, offered once, when
  it has one. Álvaro's rule is exactly this asymmetry: propose a conclusion when
  you reach a verdict, and otherwise say nothing about ending.

## Consequences

- **A quiet resting state.** The guided loop (approve → report → decide) no longer
  repaints a two-option prompt after every action; the thread reads as a
  conversation that has paused.
- **Discoverability rests on the status line and the agent's message** — the same
  two signals ADR-0042 already relied on, minus the prompt. The composer's
  placeholder ("Reply to pick it back up…") remains the in-place hint.
- **Conclude gains one entry point and loses none:** the toolbar copy now also
  covers `awaiting_operator`, where the removed banner button used to live.
- **The `stopped` banner drops its "or conclude the retest yourself" clause** for
  the same reason; it states the state and stops.
- **Legacy transcripts are unaffected** — no state, event, or API change. Only
  message text and the console's rendering differ.
