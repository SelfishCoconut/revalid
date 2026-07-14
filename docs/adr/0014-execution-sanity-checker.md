# 0014. Execution sanity checker: an independent verifier over the approved-plan execution boundary

Date: 2026-07-14
Status: proposed

## Context

FR-08 (Must) requires *an independent verifier* that monitors execution against
the approved plan and the finding's intent, detecting (a) deviation from the
approved plan and (b) ambiguous outcomes — the model rationalizing between
"vulnerability patched" and "endpoint changed/moved" — forcing the verdict to
*inconclusive* with a stated reason instead of a guess. ADR-0002 already names a
"sanity checker" as part of the plan-approve-execute architecture; this ADR
fixes its design.

Two forces make a *separate* verifier worthwhile even though today's pieces
partly cover the ground:

- **Plan deviation.** The FR-05 chokepoint (`execute_approved_plan`, ADR-0012)
  reads probes straight from the approved `PlanRecord`, so it is *structurally*
  faithful — it can only run what was approved. But "the executor never runs an
  off-plan action" is a safety invariant on the critical network path, and a
  structural guarantee is not the same as an *enforced* one: a future
  LLM-driven executor, a refactor, or corrupted state could break it silently.
- **Ambiguous "fixed".** Each probe's assessor (`retest.py`) decides
  still-open / fixed / inconclusive from its own evidence. The SQLi assessor
  already maps 404 → inconclusive `endpoint_changed`, but that correctness lives
  inside one assessor. Nothing stops a future or naive assessor from returning
  *fixed* on evidence that cannot tell a real fix from a moved/absent endpoint —
  exactly the confidently-wrong verdict NFR-01 forbids.

An independent layer that (a) enforces plan membership at the execution boundary
and (b) reviews every verdict for over-confident *fixed* addresses both, and
does so in one place that is trivially unit-testable and cannot be bypassed by
adding probe kinds.

## Decision

We will add `src/revalid/sanity.py`, an independent verifier, and route all
plan execution through it. It exposes one execution primitive —
`guarded_run(client, probe, approved)` — which the FR-05 chokepoint uses in
place of calling `run_probe` directly. `guarded_run` = *assert-in-plan* →
`run_probe` → *review-verdict*.

**(a) Plan-deviation blocking — fail-closed.** `assert_in_plan(probe, approved)`
compares the probe's canonical identity (method, URL, headers, JSON body)
against the identities of the approved plan's probes. A non-member is **blocked
and logged** (`logging` WARNING) and raises `PlanDeviationError`; the whole
retest run aborts (the API maps it to HTTP 409). A plan deviation is an
integrity fault, not a retest outcome — nothing off-plan may open a socket, and
we do not paper over it with an inconclusive verdict.

**(b) Ambiguity downgrade — conservative, one-directional.** `review_verdict`
inspects only *fixed* verdicts and downgrades them to *inconclusive* when the
evidence cannot distinguish a fix from a moved/absent endpoint:

- HTTP **404 / 410** (endpoint absent) → `endpoint_changed`
- any **3xx redirect** (not an explicit rejection) → `ambiguous_response`

A *fixed* verdict must therefore rest on a positive signal (an explicit
rejection such as 401/403, or a clear negative result), never on absence or a
redirect. The verifier only ever *downgrades* — it never turns inconclusive or
still-open into fixed, and never manufactures confidence. A `404` status is
itself proof the server answered ("the app is up"), and an unreachable target
already yields `target_unreachable` upstream, so no separate liveness probe is
needed to satisfy the FR-08 endpoint-moved criterion.

Deviations are logged now; **persisting** them into the audit trail is deferred
to FR-10 (#15), which owns the durable trail. The two FR-08 acceptance criteria
are verified at the unit level (the right pyramid layer for pure guard logic),
with the guard wired into the real `execute_approved_plan` path.

## Alternatives considered

- **Fold the checks into the assessors / the FR-05 chokepoint.** Rejected: the
  SRS asks for an *independent* verifier. Spreading "never confidently fixed on
  absence" across every assessor means each new probe kind can reintroduce the
  bug; a single independent layer cannot be bypassed by adding a probe.
- **Block-and-continue on deviation** (record an inconclusive row, run the rest
  of the plan). Rejected: a deviation means the execution set no longer matches
  what was approved — a safety fault. Fail-closed (abort + surface) is the
  honest posture; continuing would normalise an integrity violation into a data
  point.
- **Downgrade *fixed* only on literal 404/410.** Rejected in favour of also
  downgrading on 3xx: a redirect is not an explicit rejection and can mask an
  endpoint move or an auth wall, so a *fixed* resting on it is not trustworthy.
- **A separate liveness probe** to prove "the app is up" before ruling
  endpoint-moved. Rejected as redundant: a 404 response already proves the
  server answered, and unreachability is already `target_unreachable`. An extra
  network call would couple the verifier to I/O for no gain in verdict
  correctness (both branches are inconclusive).

## Consequences

- **Easier:** one enforced, testable boundary for the two safety invariants;
  new probe kinds inherit both guarantees for free; NFR-01's "zero confidently
  wrong on ambiguity" gets a structural backstop.
- **Harder / accepted debt:** `assert_in_plan` is redundant-by-construction on
  today's chokepoint (defence-in-depth), so its failure path is reachable only
  via the guard's own API and tests, not the live UI flow — an accepted cost of
  fail-closed safety. Deviation events are only logged until FR-10 adds
  persistence. `review_verdict` is a no-op for today's assessors (which never
  over-claim *fixed*); its value is guarding future ones.
- The FR-05 chokepoint gains a dependency on `sanity`; `run_probe` is no longer
  called anywhere except through `guarded_run`.
