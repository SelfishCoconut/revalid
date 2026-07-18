# 0030. Agentic verdict integration + human adjudication (FR-17 Slice 6a)

Date: 2026-07-18
Status: proposed

## Context

FR-17 Slice 6a (epic [#87](https://github.com/SelfishCoconut/revalid/issues/87),
issue [#102](https://github.com/SelfishCoconut/revalid/issues/102)) wires the
agentic console's verdict into the three places a verdict matters in this tool —
the `verdicts` table (FR-09), the audit re-derivation (FR-10), and the run export
(FR-12) — and lets the human **adjudicate** it (accept or override). Until now the
agentic verdict lived only on the session row + transcript, so an agentic retest
produced nothing the FR-15 evaluation could grade and the human had no way to
confirm or overturn the agent's call.

The load-bearing tension: the domain `Verdict` (FR-09) is frozen and requires
**exactly one** `Evidence` — a single request/response. That is right for a batch
probe and is what makes FR-10 re-derivation a pure function. An **agentic** verdict
is different: it is the human-adjudicated conclusion of a *multi-command
investigation* whose justification is the whole transcript, not one
request/response, and it is not a deterministic function of any single evidence
blob (ADR-0025 already recorded this NFR-02 shift).

This is Slice 6a; retiring the old FR-04/05/07-09 batch path is the follow-up
Slice 6b — 6a is purely additive.

## Decision

1. **Polymorphic storage; the domain `Verdict`/`Evidence` is untouched.** Only the
   storage row `VerdictRecord` widens: a `source` discriminator (`"batch"` /
   `"agentic"`), a nullable `session_id` FK, and a now-nullable `evidence` column.
   One table holds both shapes; the frozen FR-09 type keeps its
   evidence-required invariant. `to_domain()` stays batch-only (it raises on an
   evidence-free row); export and audit branch on `source` first.
2. **Auto-persist on conclude; the agent's verdict reaches `verdicts` with no
   human action.** `record_verdict` — the single place a session verdict is set,
   firing on a normal conclude *and* a budget give-up — also writes an agentic
   `VerdictRecord` (`actor="agent"`). This is what lets a headless free-launch run
   (Slice 5) produce a measurable outcome; a given-up session records an
   inconclusive verdict, which the eval buckets as a safe hedge.
3. **Adjudication appends a superseding record; the agent's is never mutated.**
   `POST /api/retest-sessions/{id}/adjudicate {status, rationale}` appends a
   `verdict_adjudicated` transcript event **and** a second `VerdictRecord`
   (`actor="operator"`, higher id ⇒ wins latest-per-finding). Append-only, so
   FR-10 stays intact. **Accept** records the agent's own call (so the audit trail
   shows a human reviewed and confirmed); **Override** records a different one.
4. **FR-10 audit re-derives agentic rows from the transcript.** `rederive_run`
   branches on `source`: batch rows re-derive from evidence exactly as before;
   agentic rows are re-projected from the authoritative transcript event (the
   `verdict` event for the agent's record, the latest `verdict_adjudicated` for an
   operator record) and diffed. A drift between the stored row and the transcript
   it projects is a discrepancy — a denormalization-integrity check, honest to
   ADR-0025's "reproducibility = replayable transcript" reframing.
5. **FR-12 export flattens `VerdictExport`.** It carries the verdict fields
   directly (+ `source`/`session_id`/optional `evidence`) rather than embedding
   the domain `Verdict`, so one shape covers both. `SCHEMA_VERSION` 1.1 → 1.2
   (published schema regenerated + drift-tested). The API's `VerdictOut` flattens
   the same way — a superset of the pre-6a batch fields, so existing batch
   consumers are unaffected, and agentic verdicts are now queryable at
   `GET /api/verdicts`.

## Alternatives considered

- **Reshape the domain `Verdict`** (optional / list `evidence`) — rejected: it
  weakens the FR-09 type invariant for *every* verdict, batch included, to serve
  the agentic case.
- **A parallel `AgenticVerdict` type + table** — rejected: duplicates the finding
  link, the audit, and the export plumbing; two of everything.
- **No-op "Accept"** (leave the agent's record standing) — rejected: an explicit
  operator record makes the human review auditable, which is the whole point of
  the human-in-the-loop contribution.
- **Keep `VerdictExport` embedding `Verdict`, bolt agentic fields alongside** —
  rejected: two ways to read a verdict's status; the flatten is the shape Slice 6b
  converges on anyway (batch removed → embedded `Verdict` would be the only
  remaining case), so flattening now avoids reshaping twice.

## Consequences

- **Good:** an agentic session now produces a first-class verdict — queryable
  (FR-09), auditable (FR-10), exportable (FR-12) — and a human can accept or
  override it. The FR-15 evaluation can finally score the agentic path.
- **NFR-02 (reproducibility):** consistent with ADR-0025 — a batch verdict
  re-derives from its evidence; an agentic verdict re-derives from its transcript.
  The audit proves the stored row still equals its source of truth in both cases.
- **Accepted limitations:** an agentic verdict carries no single-request timing,
  so the export's `total_elapsed_ms` sums evidence-backed (batch) verdicts only
  (agentic timing lives in the transcript). The domain-level FR-09 "no verdict
  without evidence" invariant now holds only for batch verdicts; for agentic ones
  the storage-level invariant is "no verdict without a session transcript"
  (`source="agentic"` ⇒ `session_id` set), enforced by the `agentic()` constructor.
- **Invariants preserved:** the frozen `Verdict`/`Evidence` type, the batch path
  (still fully operational until Slice 6b), command/plan gating, and the egress
  lock (NFR-03) are all unchanged.

## References

- Design spec: `docs/superpowers/specs/2026-07-18-agentic-retest-console-slice-6a-design.md`
- Plan: `docs/superpowers/plans/2026-07-18-agentic-retest-console-slice-6a.md`
- Builds on ADR-0025 (agentic console + NFR-02 reproducibility reframing), ADR-0016 (FR-12 export), ADR-0015 (FR-10 audit); epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#102](https://github.com/SelfishCoconut/revalid/issues/102)
