# FR-17 Slice 6a — agentic verdict integration + human adjudication (design)

- **Issue:** #102 · **Epic:** #87 · **ADR:** 0030 (proposed) · **Milestone:** M6
- **Depends on:** Slices 0–5 (agentic console, free-launch). **Followed by:** Slice 6b (retire the batch path).
- **Date:** 2026-07-18

## 1. Problem

The agentic console (Slices 0–5) drives an agent to a verdict, but that verdict is a
dead end: it is written only to the session row (`verdict_status`/`verdict_rationale`)
and the append-only transcript (a `verdict` event). It never reaches the three places a
verdict *matters* in this tool:

- **FR-09** — the `verdicts` table (`GET /api/verdicts`, the canonical verdict store).
- **FR-10** — `rederive_run`, the audit that reproduces every verdict from the trail.
- **FR-12** — `RunExport`, the document the FR-15 evaluation harness scores.

So an agentic retest produces **no measurable outcome**: a headless free-launch run
(Slice 5's whole point) yields nothing the evaluation can grade. And the human, having
watched the agent conclude, has no way to **adjudicate** — accept the agent's call or
override it — even though FR-17's contribution is precisely *human-in-the-loop* judgment.

Slice 6a closes both gaps. It is **purely additive**: the batch path keeps working
untouched; Slice 6b retires it later.

## 2. Core tension and the decision

The domain `Verdict` (FR-09, `domain.py`) is frozen and requires **exactly one**
`Evidence` — a single HTTP request/response. That invariant is *right* for a batch probe
(one probe → one evidence → one verdict) and it is what makes FR-10 re-derivation a pure
function. An **agentic** verdict is a different animal: it is the human-adjudicated
conclusion of a *multi-command investigation*; its justification is the whole transcript,
not one request/response, and it is not a deterministic function of any single evidence
blob (ADR-0025 already recorded this NFR-02 shift).

Three ways to reconcile were considered:

1. **Reshape `Verdict`** to make `evidence` optional / a list. Rejected — it weakens the
   FR-09 type invariant for *every* verdict, batch included, to serve the agentic case.
2. **A parallel `AgenticVerdict` type + table.** Rejected — duplicates the finding link,
   the audit, and the export plumbing; two of everything.
3. **Polymorphic storage (chosen).** Keep the frozen domain `Verdict`/`Evidence`
   *exactly as is*. Widen only the **storage** row `VerdictRecord` with a `source`
   discriminator so one table holds both shapes. The domain type stays honest; the
   storage row absorbs the variance.

**Álvaro's decisions (locked):** polymorphic storage; auto-persist the agent verdict on
conclude, human override **appends** a superseding record. Latest-per-finding is
authoritative; append-only, so FR-10 stays intact.

## 3. Data model — polymorphic `VerdictRecord`

`VerdictRecord` (`db.py`) gains three columns and one constructor. The frozen domain
`Verdict`/`Evidence` are **not touched**.

| Column | Type | Batch | Agentic |
|---|---|---|---|
| `source` | `str` (`String(16)`, default `"batch"`) | `"batch"` | `"agentic"` |
| `session_id` | `int \| None` FK → `retest_sessions.id`, default `None` | `None` | the session |
| `evidence` | `dict \| None` (**now nullable**) | the request/response | `None` |

Agentic rows also set `probe_kind="agentic"`, `reason_code="agentic_conclusion"`
(agent) / `"operator_adjudication"` (override), `matched_indicators=[]`, `plan_id/plan_version=None`.

- `from_domain(...)` is unchanged — it stays the **batch** constructor (evidence-required).
- New `VerdictRecord.agentic(finding_id, session_id, status, rationale, *, actor, reason_code)`
  builds an agentic row directly, bypassing the evidence-required path.
- `to_domain()` is unchanged and is **only ever called on batch rows** (it would raise on
  a null-evidence agentic row). Export and audit branch on `source` before calling it.

No Alembic (ADR-0002 / project convention): a stale `revalid.db` is deleted and recreated
by `create_all`. Documented in "How to validate".

## 4. Auto-persist — the agent's verdict reaches `verdicts`

`record_verdict(session, session_id, status, rationale)` in `retest_session.py` is the
**single** place a session verdict is set — it fires on a normal conclude *and* on the
budget give-up (`_give_up` calls it). It already loads the `RetestSessionRecord` (so it
has `finding_id`). We add, right after it stamps the session row, one insert:

```
VerdictRecord.agentic(
    finding_id=record.finding_id, session_id=session_id,
    status=status, rationale=rationale,
    actor="agent", reason_code="agentic_conclusion",
)
```

Consequences, all intended:
- A concluded session → a queryable agentic verdict, no human action needed. This is what
  lets a **headless free-launch** run (Slice 5) produce something FR-15 can score.
- A **given-up** session (budget exhausted, inconclusive) also writes a row — an
  inconclusive agentic verdict, which the eval correctly buckets as a safe hedge.

## 5. Adjudication — accept / override

New endpoint `POST /api/retest-sessions/{id}/adjudicate` with body `{status, rationale}`,
valid only on a **terminal** session that already has a verdict. It is a pure DB
operation — the session is already torn down, so it never touches the live registry:

1. Append a `verdict_adjudicated` transcript event (`{status, rationale}`) — new
   `SessionEventKind.VERDICT_ADJUDICATED`.
2. Insert a **superseding** `VerdictRecord.agentic(... actor="operator",
   reason_code="operator_adjudication")`. Higher id ⇒ wins latest-per-finding.

The agent's row is never mutated (append-only; FR-10 intact).

- **Accept** (SPA) posts the agent's own status+rationale → an explicit operator record.
  Chosen over a no-op so the audit trail records that a human *did* review and confirm.
- **Override** posts a different status/rationale.

`verdict_status`/`verdict_rationale` on the session row are also updated so the SPA's
session view reflects the final call; the transcript + verdict rows are the source of truth.

## 6. FR-10 audit — transcript integrity for agentic rows

`rederive_run` (`audit.py`) branches on `source`:

- **batch** → exactly today: `rederive_verdict(probe_kind, evidence)` and diff.
- **agentic** → reproduce the verdict **from the transcript** (the audit trail *is* the
  transcript here, per ADR-0025). The authoritative event is picked by actor:
  - `actor="agent"` → the session's `verdict` event.
  - `actor="operator"` → the session's latest `verdict_adjudicated` event.

  Diff `(row.status, row.rationale)` against that event. A mismatch — a `VerdictRecord`
  that has drifted from the transcript it projects — is a `Discrepancy`. (This is a
  denormalization-integrity check; it is arguably *stronger* than the batch case because
  it proves the stored verdict still equals its source of truth.)

`Discrepancy` gains no new fields; the agentic branch formats `stored`/`rederived` as
`f"{status}/{reason_code}"` the same way, using the transcript-derived status.

## 7. FR-12 export — flatten `VerdictExport`, bump to 1.2

`VerdictExport` currently **embeds** the frozen domain `Verdict`
(`verdict: Verdict`). Agentic rows have no domain `Verdict`, so the export must carry the
fields directly. `VerdictExport` **flattens** to:

```
id, finding_id, probe_kind, plan_id, plan_version, actor, created_at,
source, session_id,            # new discriminator + link
status, reason_code, rationale, matched_indicators,   # was verdict.*
evidence: Evidence | None      # None for agentic
```

- One shape covers both; `_verdict_export` reads flat columns for both sources and no
  longer calls `to_domain()` at all (removing a coupling).
- `SCHEMA_VERSION` **1.1 → 1.2**; regenerate `docs/reference/schemas/run-export.schema.json`
  (`make export-schema`) and update the drift test.
- `_metrics`: `by_status[v.status.value]`; `total_elapsed_ms = sum(v.evidence.elapsed_ms
  for v if v.evidence is not None)` — agentic timing lives in the transcript, not here.
- `eval.py` reads `verdict.status` / `verdict.evidence` directly (two call sites), which
  the flatten simplifies. `latest_verdict_by_finding` already picks the highest id, so an
  operator override (later id) is authoritative for the eval with **no change**.

This is the direction Slice 6b converges on anyway (batch removed → embedded `Verdict`
would be the only remaining shape); flattening now avoids reshaping twice.

## 8. Frontend — adjudication panel

On a **terminal** session, the console shows an adjudication panel below the verdict:
- The agent's verdict (status badge + rationale).
- **Accept** — one click; records an operator verdict equal to the agent's.
- **Override** — a status picker (still-open / fixed / inconclusive) + rationale field.
- After adjudication: the final verdict + who set it (agent vs operator), panel collapses
  to a read-only summary.

`api/client.ts` gains `adjudicateSession(id, {status, rationale})`; the panel uses a
mutation that invalidates the session + events queries so the transcript (now carrying the
`verdict_adjudicated` event) and the final verdict re-render. No change to the batch
wizard — that is Slice 6b.

## 9. Scope boundary (what 6a does NOT touch)

`plan.py`, `approval.py`, `retest.py`, the batch REST endpoints, and the SPA's
plan/approve/retest wizard stages all keep working unchanged. 6a only *adds* the agentic
verdict into FR-09/10/12 and the adjudication surface. Retiring the batch path is **6b**.

## 10. Acceptance criteria (→ SRS FR-17)

1. An agentic session's verdict is queryable in `verdicts`, appears in the FR-12 export,
   and re-derives under the FR-10 audit (transcript integrity).
2. A human can accept or override the agent's verdict; the override supersedes without
   mutating the agent's record (append-only).
3. The domain `Verdict`/`Evidence` type is unchanged; batch verdicts behave exactly as
   before (existing FR-09/10/12 tests stay green).

## 11. Test plan (pyramid)

- **unit** — `VerdictRecord.agentic` shape; `record_verdict` auto-persists an agentic row
  on conclude and on give-up; `adjudicate` appends the event + a superseding operator row;
  `rederive_run` reproduces an agentic verdict and flags a tampered one; export flatten
  round-trips + validates against the 1.2 schema; `latest_verdict_by_finding` returns the
  operator row.
- **integration** — `POST …/adjudicate` end-to-end over the app (start → conclude via a
  Pydantic-AI stand-in → verdict queryable → adjudicate → export's latest = operator's).
- **frontend** — the adjudication panel (Accept / Override), disabled until terminal.
