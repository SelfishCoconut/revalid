# Agentic Retest Console — Slice 5 design (free-launch + budget/give-up)

- **Date**: 2026-07-17
- **Status**: draft (brainstorm output, approved by Álvaro to spec Slice 5)
- **Owner**: Álvaro Navarro
- **Implements**: FR-17 Slice 5 (issue [#100](https://github.com/SelfishCoconut/revalid/issues/100), epic [#87](https://github.com/SelfishCoconut/revalid/issues/87))
- **Parent design**: `docs/superpowers/specs/2026-07-16-agentic-retest-console-design.md` (§3, slice 5)
- **ADR**: **ADR-0029** (`proposed`, lands with the implementation PR)

---

## 1. Motivation

Slices 0–4 built the agentic console: an egress-locked sandbox, a gated `run_command`
loop, a guiding plan (gated `set_plan`), operator `!` commands, and chat steering. Every
agent command still stops for a human click. That is correct for careful work, but two
situations want a lighter touch:

- **Boring stretches.** Recon/enumeration on a finding can take a dozen commands before the
  interesting one. Hand-approving each is friction with no safety payoff — the sandbox already
  contains the blast radius.
- **Headless evaluation.** FR-15 (M5) scores the agentic path over an evaluation set. A human
  cannot sit and click through 8–12 findings; the harness needs to drive sessions without a
  person in the approval loop.

Slice 5 adds a **free-launch** switch (auto-approve every *command*; **plan changes stay
gated, always**), makes the previously-hardcoded **step budget** configurable and **visible**,
adds a **wall-clock budget** for the autonomous mode, and surfaces the existing **give-up**
backstop as a distinct state.

## 2. Scope

**In scope:**

- Free-launch mode, settable at session start *and* toggleable live.
- Configurable `max_steps`; new optional `max_seconds` (free-launch only).
- A budget meter + free-launch toggle + distinct given-up banner in the SPA.
- The transcript records mode changes and marks auto-approved commands.

**Out of scope (deferred to Slice 6):** verdict adjudication (human overrides the agent's
verdict); wiring the agentic verdict into the FR-09 `verdicts` table / FR-10 audit / FR-12
export; retiring the old batch path. The wall-clock budget here is a *give-up backstop*, not a
per-command timeout — the sandbox already enforces per-command timeouts (Slice 0).

**Non-goals:** multi-session concurrency tuning; changing the sandbox, the egress lock
(NFR-03), or the single-user threat model (ADR-0008); any change to the Slice 1–4 chat /
terminal / plan layout beyond the three additions above.

## 3. Design decisions

### 3.1 Free-launch reuses the gate — it does not fork the execution path

Free-launch is **not** a second way to run commands. When the orchestrator emits a *command*
proposal (`_dispatch_output` → `_emit_proposal` → `awaiting_command`), a free-launch session
immediately drives the **same** `apply_decision(approved=True)` chokepoint a human click would.
Consequences, all deliberate:

- The compare-and-swap on `pending_call_id` (`_consume_pending_call`), the step-budget check
  (`_step_budget_exhausted` in `_resume_with_decision`), and the transcript events are
  **identical** to the gated path. There is exactly one place a command runs.
- The only difference is a `{"auto": true}` flag on the `command_approved` event payload, so
  the audit trail stays honest about which commands a human vetted versus which ran under
  free-launch (FR-10 / NFR-02).
- **Plan proposals (`awaiting_plan`) are never auto-approved** — the branch checks
  `pending_kind == "command"`. A `set_plan` change always pauses for a human decision, even in
  free-launch. This is the recorded intent: trust the agent on tactical commands, keep the
  human on strategy.

**Iteration, not recursion.** In free-launch a whole reason→run→observe→run chain executes in
one background task. Auto-approval must *loop* over successive proposals rather than recurse
through `apply_decision → _resume_with_decision → _dispatch_output → apply_decision …`, so a
large `max_steps` cannot blow the Python stack. The orchestrator drives the loop: after
dispatching an agent step, while the session is `awaiting_command` **and** free-launch is on,
apply an auto-approval and dispatch the next step, until the agent concludes, a `set_plan`
pauses it, or a budget bound trips.

Each auto-step still streams its `command_proposed` / `command_approved` / `command_output`
events to the DB, so the WebSocket poll-tail (Slice 0) shows the agent working in real time —
the operator watches rather than clicks.

### 3.2 Both entry points

- **Session start** — `POST /api/findings/{id}/retest-session` gains an optional JSON body
  `{free_launch?: bool, max_steps?: int, max_seconds?: int}`, all defaulted (so existing
  no-body callers are unchanged). This is the headless entry a future FR-15 eval run uses.
- **Live toggle** — a new `POST /api/retest-sessions/{id}/free-launch` `{enabled: bool}`.
  Records a `free_launch_changed` transcript event. When it flips **on** and a command is
  currently pending, it auto-approves that pending command (the natural expectation). When it
  flips **off**, future proposals simply pause for a click again; nothing in flight is
  disturbed.

### 3.3 Budget: step (both modes) + wall-clock (free-launch only)

- **Step budget** already exists (`max_steps`, default 8; `_step_budget_exhausted` force-
  concludes `inconclusive` and marks `given_up`). Slice 5 makes it **configurable** at session
  start and **visible** in the UI. *Steps used* is **derived** by counting `command_approved`
  events — the transcript stays the single source of truth; no counter column.
- **Wall-clock budget** (`max_seconds`, nullable → a sensible default like 600s when free-
  launch is used) is checked **at step boundaries only** — the orchestrator holds control
  between agent turns, never mid-turn. It is enforced **only while free-launch is on**: in
  gated mode the clock would include human think-time and trip falsely, so it is inert there.
  Tripping it force-concludes `inconclusive` with reason `"time budget exhausted"` via the same
  `_mark_given_up` path. Measured with `time.monotonic()` from session start.

Both bounds share one exit: `record_verdict(INCONCLUSIVE, <reason>)` → `_mark_given_up` →
`_teardown`. The give-up backstop is unchanged in mechanism; Slice 5 adds the second bound and
makes both visible.

### 3.4 Give-up surfacing — no new control

`given_up` is already a terminal status (`RetestSessionStatus.GIVEN_UP`). Slice 5 does not add
a "give up" button — the human already has **End session**, and the agent gives up via a budget
bound. The work is presentational: render `given_up` distinctly from `ended` / `concluded`
("Agent gave up — budget exhausted", citing which bound), and show the budget meter so the
operator sees a session approaching its limit.

## 4. Data model

`retest_sessions` gains three columns (all with safe defaults so existing rows load):

| Column | Type | Default | Meaning |
|--------|------|---------|---------|
| `free_launch` | bool | `0` (false) | current mode; updated by the toggle endpoint |
| `max_steps` | int | `8` | step budget for this session |
| `max_seconds` | int, nullable | `NULL` | wall-clock budget (free-launch only); `NULL` = no time bound |

One new `SessionEventKind`:

- `FREE_LAUNCH_CHANGED = "free_launch_changed"` — payload `{"enabled": bool}`; the mode-change
  audit record.

Auto-approvals reuse `COMMAND_APPROVED` with `payload {"auto": true}` — no new event kind for
them (an auto-approval *is* a command approval, just not human-initiated).

`LiveSession` gains `free_launch: bool`, `max_seconds: float | None`, and a monotonic
`started_at` for the wall-clock check. `max_steps` already lives on `LiveSession`.

## 5. API surface (additions/changes)

- `POST /api/findings/{id}/retest-session` — **changed**: optional body
  `{free_launch?: bool, max_steps?: int, max_seconds?: int}`. Persisted on the new columns and
  seeded onto the `LiveSession`. No body → today's behaviour (gated, `max_steps=8`, no time
  bound).
- `POST /api/retest-sessions/{id}/free-launch` — **new**: `{enabled: bool}` → `202`. Updates
  the column + `LiveSession`, appends `free_launch_changed`, and on enable auto-approves a
  pending command. No-op (still `202`) if the session is not live.
- `GET /api/retest-sessions/{id}` — **changed**: the returned session object now carries
  `free_launch`, `max_steps`, `max_seconds` (the UI derives *steps used* from the events it
  already receives).

The WS stream is unchanged (it already tails all `session_events`, including the new kinds).

## 6. Frontend (`RetestSession.tsx`)

- **Free-launch toggle** in the session-controls row (beside **End session**): a labelled
  switch reflecting `free_launch`; flipping it calls the new endpoint. Disabled once the
  session is over.
- **Budget meter** in the session header: `steps used / max_steps` (count of
  `command_approved` events over `max_steps`), and — when free-launch is on and `max_seconds`
  is set — an elapsed-vs-`max_seconds` readout. Purely informative; approaching the bound is a
  visual cue, not a control.
- **Auto-approved commands** render in the chat with a subtle "auto" tag instead of an
  approve/reject card (the card is meaningless once it has already run).
- **Given-up banner**: a distinct terminal treatment for `given_up` (vs. `ended`/`concluded`),
  naming the bound that tripped.

No change to the center-chat / docked-terminal / plan-panel structure from Slices 1–4.

## 7. Testing

- **Unit** (`tests/unit/`, `FakeSandbox` + Pydantic AI `FunctionModel`):
  - free-launch scripts propose→propose→conclude and reach a verdict with **zero** human
    decisions; assert each command ran and each `command_approved` event carries `auto: true`.
  - a `set_plan` proposal under free-launch **still pauses** (`awaiting_plan`), proving plan
    changes stay gated.
  - the step budget bites under free-launch (force `given_up` / `inconclusive`
    "budget exhausted") — the loop cannot run past `max_steps`.
  - the wall-clock budget trips at a step boundary in free-launch and is inert in gated mode
    (monotonic clock injected/faked — no real sleeping).
  - the live toggle: enabling with a pending command auto-approves it; disabling stops future
    auto-approvals; both emit `free_launch_changed`.
- **Integration** (`tests/integration/`, marker `integration`, real REST + WS): start a session
  with `free_launch:true` and assert commands auto-run to a verdict over the wire; the toggle
  endpoint changes mode mid-session; `GET` returns the new fields.
- **Frontend** (vitest): the meter derives steps-used from events; the toggle calls the
  endpoint; auto-tagged commands render without an approval card; the given-up banner renders
  distinctly. Keep `RetestSession`-owned pure logic at its pinned coverage.
- **No new system test** — the egress lock and sandbox are unchanged; the existing nightly
  Slice 0 system test still covers them.

## 8. Safety & invariants (unchanged)

- **Egress lock (NFR-03)** and the **sandbox** are untouched — free-launch removes the *human
  pause*, not the containment. Auto-run commands go through the identical `sandbox.exec`.
- **Threat model (ADR-0008):** single trusted user, app on `127.0.0.1`. Free-launch is the
  human delegating command-level approval to the budget-bounded agent; strategy (`set_plan`)
  still needs an explicit human decision, and the human can re-arm the gate at any time.
- **Auditability (FR-10 / NFR-02):** every mode change and every auto-approval is a transcript
  event, so a replayed session shows exactly what ran under which mode — the transcript stays
  the complete record.

## 9. Process & documentation

- **ADR-0029** (`proposed`) records the decision: free-launch as gate-reuse (not a forked
  path), plan-changes-always-gated, the two budget bounds, and the NFR-02 note that auto-
  approved commands are transcript-marked. Lands with the implementation PR.
- **FR-17** in `docs/requirements/srs.md` gains Slice 5 acceptance criteria **AC9–AC12**
  (issue #100), ticked as the slice lands.
- **Roadmap** M6 Slice 5 checkbox + a state note in the same PR.
- Old batch path stays operational (retires in Slice 6).

## 10. Open questions / risks

1. **Free-launch loop latency.** On a slow local model (e.g. `qwen3.6:27b` ~50s/turn) a
   free-launch run to `max_steps=8` is minutes long; the wall-clock budget exists precisely to
   bound this. Documented, not a blocker — the operator sees the meter and can End session.
2. **Toggle-during-run race.** Flipping free-launch on exactly as a command proposal is being
   emitted: the enable path and the dispatch loop both consult `pending_call_id` under
   `live.lock` (the existing CAS), so at most one auto-approval fires for a given pending
   command. Covered by a unit test.
3. **Schema evolution.** The project has no migration tool — the DB is created with
   `Base.metadata.create_all` and, for this single-user local tool, a stale `revalid.db` is
   simply deleted and recreated (the established pattern, consistent with how prior columns
   landed). The three new columns are added to the `RetestSessionRecord` model with
   defaults; a fresh DB picks them up, and the defaults (gated / `max_steps=8` / no time
   bound) mean the model round-trips cleanly. No in-place `ALTER TABLE` is attempted.
