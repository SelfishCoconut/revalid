# 0043. Reopen a concluded retest session: withdraw the verdict, keep the transcript

Date: 2026-07-24
Status: accepted (ratified 2026-07-25)

## Context

A retest session ends when a verdict is recorded (`concluded`): the sandbox is
torn down and the row goes terminal. Live use surfaced that the operator sometimes
concludes too early — "I don't think we reached a verdict yet, I want to keep
trying" (issue #214) — with no way back. The only options were to accept the
verdict or start a brand-new session, losing the thread.

## Decision

Add **reopen**: `POST /api/retest-sessions/{id}/reopen` returns a `concluded`
session to `idle` so the operator can wake it and continue testing.

- **The verdict is withdrawn, not deleted.** For an agentic session the
  **transcript is the append-only audit** (ADR-0025 / NFR-02): reopen appends a
  `VERDICT_CANCELLED` event next to the original `VERDICT` event, so the full
  history — the verdict was recorded, then retracted — survives verbatim. What is
  removed is the row in the queryable **`verdicts` projection**, because a
  retracted verdict is no longer a *current* determination and the finding must not
  keep showing an outcome the operator withdrew. The session row's verdict fields
  are cleared and its status set to `idle` (a `state_change` event).
- **Reopen reuses the idle→wake path.** An `idle` session's wake
  (`_start_idle`) already re-provisions the sandbox and reconstructs the prompt
  from the transcript (goal + scope), so reopen adds no new provisioning code — the
  operator wakes the reopened session and the agent re-engages with the prior work
  visible in the transcript.
- **A later re-conclusion is a fresh verdict** (a new `VerdictRecord` + `VERDICT`
  event), consistent with the existing latest-per-finding-wins model (ADR-0015).

## Alternatives considered

- **Keep the `VerdictRecord`, add a `cancelled` flag.** Rejected for now: a schema
  change for a projection whose authority is already the transcript; the finding's
  "current determination" query would still have to learn to skip cancelled rows.
  Deleting the projection row keeps the current-state query trivially correct while
  the transcript holds the audit. (Revisit if verdict history needs to be queryable
  outside the transcript.)
- **Delete the whole session / start fresh.** Rejected: loses the thread and the
  transcript — the opposite of "keep trying".
- **Mutate the existing verdict in place.** Rejected: violates the append-only
  audit; adjudication (ADR-0015) already establishes append-don't-mutate.

## Consequences

- **Easier:** the operator can retract a premature verdict and continue on the same
  session; nothing about the prior work is lost.
- **Audit:** the transcript keeps `VERDICT` + `VERDICT_CANCELLED` (+ a later new
  `VERDICT` if re-concluded); the `verdicts` projection reflects only live
  determinations. The FR-10 audit invariant (transcript is the record) holds.
- **New surface:** one status transition (`concluded` → `idle`), one event kind
  (`verdict_cancelled`), one route (`…/reopen`), and a Reopen control on the
  console's verdict panel.
- **Limit:** reopen is only from `concluded` (the state that carries a verdict);
  `ended`/`error` have no verdict to withdraw.
