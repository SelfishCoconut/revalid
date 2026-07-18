# FR-17 Slice 6b-i — flexible command-output evidence for agentic verdicts (design)

- **Epic:** #87 · **ADR:** 0031 (proposed) · **Milestone:** M6
- **Depends on:** Slice 6a (agentic verdict integration). **Followed by:** 6b-ii (user-owned goal), 6b-iii (retire batch execution + UI reshape).
- **Date:** 2026-07-18

## 1. Problem

Slice 6a wired the agentic verdict into FR-09/10/12 but made it **evidence-free**
(`evidence=None`), justified only by the transcript. That was a deliberate 6a
simplification, and it is wrong for where the tool is going: the retest agent runs
**arbitrary tooling** — a Kali-style toolbox (`nmap`, `sqlmap`, `curl`, `nikto`, …),
not HTTP `Probe`s — and when it concludes it *has* a decisive piece of proof (the
command whose output settled the verdict). Throwing that away weakens the FR-09
"a verdict is linked to the evidence that justifies it" story and gives the
operator a verdict with no pinned proof to inspect.

Álvaro's direction: **don't discard the evidence slot — repurpose it.** When the
agent concludes, its verdict carries *both* the full transcript *and* a pinned
piece of proof. Because the agent's commands aren't HTTP-shaped, the evidence
must be **flexible**: the agent's explanation plus a command's real output.

## 2. Decision — a flexible, tool-agnostic evidence shape

A new frozen domain model `AgenticEvidence`, stored in the same `evidence` JSON
column the HTTP `Evidence` uses (the batch shape stays until 6b-iii retires it):

```python
class AgenticEvidence(BaseModel):
    explanation: str          # the agent's account of what proves the verdict
    command: str = ""         # the decisive command it ran (empty if it concluded without one)
    output: str = ""          # that command's captured stdout/stderr excerpt (truncated)
    exit_code: int | None = None
    elapsed_ms: float = 0.0
```

- The HTTP `Evidence` (`request_method`/`request_url`/…) is **untouched** — it stays
  the *batch* verdict's evidence until 6b-iii. One JSON column now holds either
  shape, discriminated by the row's `source` (batch → `Evidence`, agentic →
  `AgenticEvidence`), exactly as 6a's polymorphic `VerdictRecord` already keys off
  `source`.
- `explanation` reuses the verdict's `rationale` — no new `ConcludeOutput` field.
  The agent already justifies its verdict there; the evidence pairs that
  justification with the proof.

## 3. Where the proof comes from — real data, not a restatement

`record_verdict` (`retest_session.py`) — the single conclude/give-up hook — builds
the evidence, so both the normal conclude and the budget give-up produce one:

1. Query the session's **last `command_output` transcript event** (a new
   `_last_command_output(session, session_id)` helper) — the decisive command the
   agent ran. Its payload already carries `command`/`stdout`/`stderr`/`exit_code`/
   `elapsed_ms`.
2. Build `AgenticEvidence(explanation=rationale, command=…, output=<stdout+stderr
   excerpt, truncated>, exit_code=…, elapsed_ms=…)`. If the agent concluded
   without ever running a command, `command`/`output` are empty and the evidence
   is explanation-only.
3. Pass it to `VerdictRecord.agentic(..., evidence=evidence.model_dump())`.

This is **honest**: the proof is the *actual captured output* from the transcript,
not the LLM restating what it saw. It is inherently consistent with the transcript
(same source), so it composes with the 6a FR-10 transcript-integrity audit with no
extra check. Output is truncated to a bounded excerpt (mirroring
`Evidence.response_body_excerpt`) so a chatty tool (`nmap -v`) can't bloat a row.

## 4. Threading the shape through FR-09 / FR-12

- **`db.py`** — `VerdictRecord.agentic()` gains an `evidence: dict | None = None`
  parameter (it currently hardcodes `None`). `to_domain()` stays batch-only,
  untouched.
- **`export.py`** — `VerdictExport.evidence: Evidence | AgenticEvidence | None`;
  `_verdict_export` branches on `record.source` to build the right shape.
  `SCHEMA_VERSION` **1.2 → 1.3** (published schema regenerated + drift-tested).
  `_metrics.total_elapsed_ms` needs no change — both shapes expose `elapsed_ms`.
- **`app.py`** — `VerdictOut.evidence: Evidence | AgenticEvidence | None`;
  `VerdictOut.from_record` branches on `source`. `GET /api/verdicts` now returns
  the agentic proof.
- **`eval.py`** — unchanged: it reads `verdict.evidence.elapsed_ms`, present on both
  shapes.
- **FR-10 audit** — unchanged: agentic verdicts still re-derive from the transcript
  (status/rationale). The evidence is captured *from* the transcript, so it is
  consistent by construction; an explicit evidence-vs-transcript check is a
  possible later hardening, not needed here.

## 5. Frontend

- **`types.ts`** — add an `AgenticEvidence` interface; `Verdict.evidence` becomes
  `Evidence | AgenticEvidence | null`.
- **`EvidenceView.tsx`** — branch on the evidence shape (agentic rows carry
  `command`/`explanation`; batch rows carry `request_method`). For an agentic
  verdict, render the **explanation + the command + its output** (+ exit code /
  elapsed) instead of the 6a "no single-request evidence" placeholder, which this
  slice removes. Batch rendering is untouched.

## 6. Scope boundary

6b-i is **only** the evidence shape + capture + display. It does **not** touch the
goal/plan seeding (6b-ii), does **not** retire the batch execution path (6b-iii),
and does **not** change the sandbox image (the "Kali toolbox" is a separate infra
issue). The agent still runs whatever commands it runs today; this slice just
captures the decisive one as the verdict's proof.

## 7. Acceptance criteria (→ SRS FR-17)

1. A concluded agentic verdict carries flexible `AgenticEvidence` — the agent's
   explanation plus the *real* last command's output — queryable at
   `GET /api/verdicts`, present in the FR-12 export, and shown in the SPA verdict
   view.
2. The evidence is captured from the transcript (not the LLM restating it); a
   verdict reached with no command run is explanation-only and still valid.
3. The HTTP `Evidence`/batch path is unchanged; the export schema bumps 1.2 → 1.3.

## 8. Test plan (pyramid)

- **unit** — `AgenticEvidence` model; `record_verdict` builds evidence from the
  last `command_output`; conclude-without-a-command → explanation-only evidence;
  give-up → evidence with the give-up reason as explanation; `VerdictRecord.agentic`
  round-trips evidence; export flatten carries `AgenticEvidence` + validates against
  the 1.3 schema; `_metrics` sums `elapsed_ms` across both shapes.
- **integration** — start → conclude via a scripted `FunctionModel`+`FakeSandbox`
  that runs one command → `GET /api/verdicts` shows the agentic evidence (command +
  output); `GET /api/export` carries it.
- **frontend** — `EvidenceView` renders an agentic verdict's explanation + command
  + output; batch rendering unchanged.
- **updates** — the 6a tests asserting `evidence is None` for agentic verdicts
  (unit `test_export_carries_agentic_verdict`, the integration verdict check, the
  `EvidenceView` "no evidence" test) flip to assert the populated evidence.
