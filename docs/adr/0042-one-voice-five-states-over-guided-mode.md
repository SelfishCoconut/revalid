# 0042. One agent, one voice, five states: fold the parallel Q&A and `needs_guidance` into the guided-mode console

Date: 2026-07-24
Status: accepted

## Context

The FR-17 retest console had grown to a good *behaviour* — guided mode drives one
action then hands back (ADR-0040), free-launch drives to a verdict (ADR-0029),
in-flight work is controllable (ADR-0039), and scope can be an online host via an
egress proxy (ADR-0041). But its *interaction surface* still carried accidental
complexity that stopped it reading like an agentic CLI (Claude Code), which is the
model Álvaro wants:

1. **A parallel read-only Q&A agent.** While the main agent is busy, an operator
   message is answered *immediately* by a separate stand-in (`build_qa_agent` /
   `answer_operator_question`), so **two voices** reply to one message. It exists
   only to paper over the approval gate freezing the run.
2. **Eight lifecycle states, several redundant.** `running_command` is **never
   set** (a command runs inside the `working`/`thinking` turn). `starting` is a
   blink. And `needs_guidance` and `awaiting_operator` are two banners over the
   *same* resume path — the agent has handed back either way.
3. **The agent shrugs at questions.** ADR-0039/#215 made any non-instruction
   message get an `AwaitOperator` reply, but its rule is "say what you *can't*
   (you don't know an internal IP)" — it will not run a command to find out.

Álvaro's direction, after weighing this branch's redesign against `main`: keep
`main`'s guided mode and online-target egress (they are deliberate, tested wins),
but adopt the redesign's **one voice**, its **five-state model**, its
**message-at-the-gate steering**, and a **general "find out and act"** stance for
questions. Tracked as issue #217.

## Decision

Reshape the console's interaction surface onto Claude Code's model, leaving guided
mode's *behaviour* intact.

- **One agent, one voice.** Delete the parallel read-only Q&A entirely
  (`build_qa_agent`, `answer_operator_question`, `_qa_context`, the `QaAgent`
  dependency and wiring). A message to a **working** agent is queued and answered
  by the *same* agent at the next turn boundary.

- **Five non-terminal states** (plus terminals `concluded`/`ended`/`error`, legacy
  `given_up`): `idle` · `working` (was `thinking`/`starting`/the dead
  `running_command`) · `awaiting_command` · `awaiting_operator` · `stopped`.
  **`needs_guidance` folds into `awaiting_operator`.** Every hand-back — a
  conversational reply, a guided one-action report with a suggested next step, a
  verdict *recommendation*, or "I've exhausted my options" — is surfaced as an
  ordinary `agent_message` and parks in `awaiting_operator`. `_pause_for_guidance`
  and the `needs_guidance` event are removed; the guided framing helpers
  (`_suggestion_reason`, `_recommendation_reason`, the `after_command` one-action
  park) are unchanged — they now feed `_await_operator`.

- **One message-routing rule** (`run_message`): `idle` → provision + run;
  `awaiting_operator`/`stopped` → resume; `awaiting_command` → **withdraw the
  pending command and steer** (Claude Code's "type at the permission prompt");
  `working` → queue; terminal → no-op. The invariant — a queued message is always
  delivered at the next turn boundary, pre-empting an approval gate — is realised
  by generalising the free-launch loop `_drive_auto` into `_advance` (message
  delivery **and** auto-approve).

- **Questions trigger a lookup, generally.** The instructions are retuned: a
  question the agent cannot answer from context is a request to **find out** — it
  works out which single command would reveal the answer and proposes that
  (still gated), rather than replying "I can't". No hardcoded examples; the agent
  reasons about how to get the information and acts.

- **Guided mode (ADR-0040), free-launch (ADR-0029), and scope egress (ADR-0041)
  are unchanged in behaviour** — guided mode simply parks in `awaiting_operator`
  now, and still never self-records a verdict; only the operator concludes.

This **amends** ADR-0034 (retires the `needs_guidance` state/banner; the
pause-and-ask lifecycle survives under `awaiting_operator`), ADR-0035
(`thinking` → `working`) and ADR-0040 (guided park renamed), and **extends**
ADR-0039 (`AwaitOperator` becomes the single hand-back path; a message can steer a
gate). It **supersedes ADR-0028's** read-only Q&A stand-in.

## Alternatives considered

- **Keep `needs_guidance` as a distinct "the agent is stuck" state.** Rejected: it
  and `awaiting_operator` share one resume path; the only difference was a banner,
  and the agent's own message says whether it is stuck, guiding, or chatting.

- **Keep the parallel Q&A, but route "real" messages to the main agent.**
  Rejected: two voices remain for the mid-turn case, and the frozen gate still
  cannot converse — the core problem is untouched.

- **Keep ADR-0039/#215's "say what you can't" for questions.** Rejected by Álvaro:
  a supervised agent that *can* find the answer with one command should do so, not
  shrug. The gate still means nothing runs without approval, so "find out" is safe.

- **Adopt this branch's *whole* redesign over `main`.** Rejected: `main`'s guided
  mode and online-target egress are deliberate, tested designs; only the
  interaction-surface pieces above are worth taking. (This is why the work was
  rebuilt on current `main` rather than merged from a stale branch.)

## Consequences

- **A console that reads like an agentic CLI:** one voice, five states, one
  message rule — with guided mode's "your move" resting state intact (it *is*
  `awaiting_operator` now).
- **A message at the approval gate withdraws the pending command** (recorded as a
  `command_rejected` set-aside) and steers; a plain resume that keeps the pending
  command is the separate `/resume` route.
- **The "needs your guidance" banner is gone**; discoverability of a hand-back now
  rests on the agent's message plus a light "your move — reply or record the
  verdict" prompt in `awaiting_operator`.
- **Legacy DB rows** in the removed states (`thinking`/`starting`/
  `running_command`/`needs_guidance`) will not load. Accepted under the single-user
  dev-tool model (ADR-0008): the local `revalid.db` is disposable (`rm` it); no
  migration is written.
