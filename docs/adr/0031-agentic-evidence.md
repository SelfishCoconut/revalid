# 0031. Flexible command-output evidence for agentic verdicts (FR-17 Slice 6b-i)

Date: 2026-07-18
Status: proposed

## Context

Slice 6a (ADR-0030) wired the agentic verdict into FR-09/10/12 but made it
**evidence-free** (`evidence=None`), justified only by the transcript. That was a
deliberate 6a simplification, and it is wrong for where the tool is going: the
retest agent runs **arbitrary tooling** — a Kali-style toolbox (`nmap`, `sqlmap`,
`curl`, `nikto`, …), not HTTP `Probe`s — and when it concludes it *has* a decisive
piece of proof: the command whose output settled the verdict. Discarding it
weakens the FR-09 "a verdict is linked to the evidence that justifies it" story and
leaves the operator a verdict with no pinned proof to inspect.

The domain `Evidence` (ADR-0002) is HTTP-specific (`request_method`/`request_url`/
`response_status`/…), so it cannot represent the output of an arbitrary command.

## Decision

1. **Repurpose the evidence slot; don't discard it.** An agentic verdict carries
   *both* the full transcript (6a) *and* a pinned piece of proof.
2. **A flexible, tool-agnostic evidence shape.** A new frozen `AgenticEvidence`
   (`explanation`, `command`, `output`, `exit_code`, `elapsed_ms`) is stored in the
   same `evidence` JSON column the HTTP `Evidence` uses, discriminated by the row's
   `source` (6a's polymorphic `VerdictRecord` already keys off it). The HTTP
   `Evidence` is **untouched** — it stays the batch verdict's shape until 6b-iii
   retires the batch path.
3. **`explanation` reuses the verdict's `rationale`.** The agent already justifies
   its verdict there; the evidence pairs that justification with the proof, so no
   new `ConcludeOutput` field is needed.
4. **Capture real data, not a restatement.** `record_verdict` — the single
   conclude/give-up hook — builds the evidence from the transcript's *last*
   `command_output` event (the decisive command's actual captured
   command/stdout/stderr/exit/timing). It is honest (the real output, not the LLM
   restating it) and inherently consistent with the transcript the FR-10 audit
   checks, so it needs no extra integrity check. Output is truncated to the same
   `16_384`-char cap the HTTP probe body uses. A verdict reached with no command run
   is explanation-only and still valid.
5. **Thread the union through FR-09/FR-12.** `VerdictExport.evidence` and the API's
   `VerdictOut.evidence` become `Evidence | AgenticEvidence | None`, branching on
   `source`; `SCHEMA_VERSION` bumps 1.2 → 1.3 (published schema regenerated +
   drift-tested). The SPA `EvidenceView` renders the agentic explanation + command
   + output.

## Alternatives considered

- **Keep agentic verdicts evidence-free (6a's choice)** — rejected: the agent has
  real proof at conclusion; throwing it away weakens FR-09 and the operator's
  ability to inspect the determination.
- **Force the agent's proof into the HTTP `Evidence` shape** — rejected: the agent
  runs arbitrary tools, not HTTP requests; most commands have no `request_method`/
  `response_status`.
- **Have the agent restate the evidence in its `ConcludeOutput`** — rejected: an
  LLM restating what it saw can drift from what actually happened; capturing the
  real `command_output` from the transcript is the honest source of truth.
- **Generalise the existing `Evidence` model** to hold both shapes — rejected: it
  would loosen the FR-09 HTTP evidence invariant for the batch path that 6b-iii is
  about to remove anyway; a separate `AgenticEvidence` keeps each shape honest.

## Consequences

- **Good:** an agentic verdict now carries inspectable, tool-agnostic proof — the
  agent's explanation plus the decisive command's real output — queryable at
  `GET /api/verdicts`, in the FR-12 export, and shown in the SPA. FR-09's
  "verdict linked to evidence" holds for agentic verdicts too.
- **NFR-02 (reproducibility):** consistent with ADR-0025/0030 — the proof is
  captured *from* the transcript, so it stays part of the one replayable record;
  the FR-10 audit still re-derives the verdict from the transcript.
- **Accepted limitations:** the evidence pins a single decisive command (the last
  one run), not the whole tool session — the full sequence remains in the
  transcript. Command output is truncated to an excerpt (a chatty tool's full dump
  is not stored on the verdict row).
- **Invariants preserved:** the HTTP `Evidence`/batch path, the frozen domain
  `Verdict`, command/plan gating, and the egress lock (NFR-03) are unchanged; the
  batch path retires later in 6b-iii.

## References

- Design spec: `docs/superpowers/specs/2026-07-18-agentic-retest-console-slice-6b-i-design.md`
- Plan: `docs/superpowers/plans/2026-07-18-agentic-retest-console-slice-6b-i.md`
- Builds on ADR-0030 (agentic verdict integration), ADR-0025 (agentic console + NFR-02 reframing); epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#104](https://github.com/SelfishCoconut/revalid/issues/104). Kali-tooling sandbox image tracked in [#105](https://github.com/SelfishCoconut/revalid/issues/105).
