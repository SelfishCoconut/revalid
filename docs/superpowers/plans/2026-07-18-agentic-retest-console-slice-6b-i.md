# FR-17 Slice 6b-i — Flexible Command-Output Evidence (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every code task (test first, watch it fail, implement, watch it pass). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an agentic verdict flexible, tool-agnostic evidence — the agent's explanation plus the real last command's output — captured from the transcript on conclude, and thread it through FR-09 (`/verdicts`), FR-12 (export), and the SPA verdict view.

**Architecture:** A new frozen `AgenticEvidence` domain model is stored in the same `evidence` JSON column the HTTP `Evidence` uses, discriminated by the row's `source` (batch → `Evidence`, agentic → `AgenticEvidence`). `record_verdict` — the single conclude/give-up hook — builds it from the transcript's last `command_output` event (real captured data). The HTTP `Evidence`/batch path is untouched (it retires in 6b-iii).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (SQLite, `create_all` — no Alembic), Pydantic AI (TestModel/FunctionModel in tests), pytest; React/TS/Vite/Tailwind, vitest.

## Global Constraints

- Python 3.12+, managed with `uv`; run tools via `uv run` / `make`.
- `mypy --strict` must pass; ruff lint + format (line length 100, Google docstrings on public API).
- Complexity gate: xenon max absolute **C**; refactor, never suppress.
- Coverage ≥ 80% on `src/`; new pure/logic lines aim for 100%.
- Tests per pyramid level: `tests/unit/` (no I/O, LLM via FunctionModel), `tests/integration/` (marker `integration`, real REST + `FakeSandbox`).
- Conventional Commits; every commit carries `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Frontend gates: eslint + `tsc` + `vite build` + vitest all green; owned pure logic stays pinned per the two-tier coverage floor.
- The frozen HTTP `Evidence`/`Verdict`, the batch path, the egress lock (NFR-03), and the single-user model (ADR-0008) are **untouched**.
- Branch: `feat/fr17-agentic-evidence-slice6b-i`. PR body must contain `Closes #104`.
- Output excerpt cap: reuse the existing convention `16_384` chars (cf. `retest._BODY_EXCERPT_LIMIT`).

---

## File Structure

**Backend:**
- `src/revalid/domain.py` — add the `AgenticEvidence` model.
- `src/revalid/db.py` — `VerdictRecord.agentic()` gains an `evidence: dict | None = None` param.
- `src/revalid/retest_session.py` — `_last_command_output` + `_build_agentic_evidence` helpers; `record_verdict` passes the built evidence.
- `src/revalid/export.py` — `VerdictExport.evidence: Evidence | AgenticEvidence | None`; `_verdict_export` branches on `source`; `SCHEMA_VERSION` 1.2 → 1.3.
- `src/revalid/app.py` — `VerdictOut.evidence: Evidence | AgenticEvidence | None`; `VerdictOut.from_record` branches on `source`.

**Frontend:**
- `frontend/src/api/types.ts` — add `AgenticEvidence`; `Verdict.evidence: Evidence | AgenticEvidence | null`.
- `frontend/src/components/EvidenceView.tsx` — render the agentic shape (explanation + command + output).

**Docs / schema:**
- `docs/reference/schemas/run-export.schema.json` — regenerate (`make export-schema`).
- `docs/adr/0031-agentic-evidence.md` (proposed) + `docs/adr/README.md`; SRS FR-17 AC; roadmap.

---

## Task 1: `AgenticEvidence` domain model

**Files:**
- Modify: `src/revalid/domain.py` (add after the `Evidence` class)
- Test: `tests/unit/test_domain.py` (create if absent, else append)

**Interfaces:**
- Produces: `AgenticEvidence(explanation: str, command: str = "", output: str = "", exit_code: int | None = None, elapsed_ms: float = 0.0)` — frozen Pydantic model.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_domain.py` (create with the import line if the file doesn't exist):

```python
from revalid.domain import AgenticEvidence


def test_agentic_evidence_defaults_to_explanation_only() -> None:
    ev = AgenticEvidence(explanation="login bypass still returns a token")
    assert ev.explanation == "login bypass still returns a token"
    assert ev.command == ""
    assert ev.output == ""
    assert ev.exit_code is None
    assert ev.elapsed_ms == 0.0


def test_agentic_evidence_carries_command_proof() -> None:
    ev = AgenticEvidence(
        explanation="200 + JWT",
        command="curl -s http://lab/rest/user/login",
        output='{"authentication":{"token":"eyJ..."}}',
        exit_code=0,
        elapsed_ms=42.0,
    )
    assert ev.command.startswith("curl")
    assert ev.exit_code == 0
    assert ev.model_config["frozen"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_domain.py -q`
Expected: FAIL — `ImportError: cannot import name 'AgenticEvidence'`.

- [ ] **Step 3: Implement the model**

In `src/revalid/domain.py`, add immediately after the `Evidence` class:

```python
class AgenticEvidence(BaseModel):
    """Flexible proof backing an agentic verdict (FR-17 Slice 6b) — tool-agnostic.

    An agentic retest runs arbitrary tooling (not just HTTP probes), so its
    evidence is the agent's explanation plus the decisive command's real output,
    not a structured request/response. The orchestrator captures it on conclude
    from the transcript's last ``command_output`` (real data, not the model
    restating it); ``command``/``output`` are empty when the agent concluded
    without running a command.

    Attributes:
        explanation: The agent's account of what proves the verdict (its rationale).
        command: The decisive command the agent ran.
        output: That command's captured stdout/stderr excerpt (truncated).
        exit_code: The command's exit status, or ``None`` when no command ran.
        elapsed_ms: The command's wall-clock time in milliseconds.
    """

    model_config = ConfigDict(frozen=True)

    explanation: str
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    elapsed_ms: float = 0.0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_domain.py -q && uv run mypy --strict src/revalid/domain.py && uv run ruff check src/revalid/domain.py tests/unit/test_domain.py`
Expected: PASS; mypy clean; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/domain.py tests/unit/test_domain.py
git commit -m "feat(domain): add AgenticEvidence — flexible tool-agnostic verdict proof (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `VerdictRecord.agentic()` stores evidence

**Files:**
- Modify: `src/revalid/db.py` (the `agentic` classmethod, ~line 275)
- Test: `tests/unit/test_db_plan.py` (append)

**Interfaces:**
- Consumes: `AgenticEvidence` (Task 1).
- Produces: `VerdictRecord.agentic(*, finding_id, session_id, status, rationale, actor, reason_code, evidence: dict[str, Any] | None = None)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_db_plan.py`:

```python
def test_agentic_constructor_stores_evidence() -> None:
    """The agentic() constructor persists a flexible evidence dict (Slice 6b-i)."""
    from revalid.domain import AgenticEvidence

    evidence = AgenticEvidence(
        explanation="still open", command="curl -s http://lab/x", output="{token}", exit_code=0
    )
    record = VerdictRecord.agentic(
        finding_id=1,
        session_id=5,
        status=VerdictStatus.STILL_OPEN,
        rationale="still open",
        actor="agent",
        reason_code="agentic_conclusion",
        evidence=evidence.model_dump(),
    )
    assert record.evidence is not None
    assert record.evidence["command"] == "curl -s http://lab/x"
    assert record.evidence["explanation"] == "still open"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_db_plan.py::test_agentic_constructor_stores_evidence -q`
Expected: FAIL — `TypeError: agentic() got an unexpected keyword argument 'evidence'`.

- [ ] **Step 3: Implement the parameter**

In `src/revalid/db.py`, change the `agentic` classmethod signature and body. Replace:

```python
    @classmethod
    def agentic(
        cls,
        *,
        finding_id: int,
        session_id: int,
        status: VerdictStatus,
        rationale: str,
        actor: str,
        reason_code: str,
    ) -> VerdictRecord:
```

with (add the `evidence` param) and set `evidence=evidence` in the `cls(...)` call (replacing the hardcoded `evidence=None`):

```python
    @classmethod
    def agentic(
        cls,
        *,
        finding_id: int,
        session_id: int,
        status: VerdictStatus,
        rationale: str,
        actor: str,
        reason_code: str,
        evidence: dict[str, Any] | None = None,
    ) -> VerdictRecord:
```

In its body, change `evidence=None,` to `evidence=evidence,`.

Update the docstring's first line to note the evidence: append a sentence — `` ``evidence`` is the flexible :class:`~revalid.domain.AgenticEvidence` proof (Slice 6b-i), or ``None`` when unavailable.``

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_db_plan.py -q && uv run mypy --strict src/revalid/db.py`
Expected: PASS; mypy clean. (The existing `agentic()` callers pass no `evidence` → default `None`, unchanged.)

- [ ] **Step 5: Commit**

```bash
git add src/revalid/db.py tests/unit/test_db_plan.py
git commit -m "feat(db): VerdictRecord.agentic() stores flexible evidence (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `record_verdict` builds evidence from the last command output

**Files:**
- Modify: `src/revalid/retest_session.py` (`record_verdict`, ~line 140; add two helpers above it)
- Test: `tests/unit/test_retest_session.py` (append)

**Interfaces:**
- Consumes: `AgenticEvidence` (Task 1), `VerdictRecord.agentic(..., evidence=...)` (Task 2).
- Produces: private `_last_command_output(session, session_id) -> dict[str, Any] | None`, `_build_agentic_evidence(session, session_id, rationale: str) -> AgenticEvidence`. `record_verdict` signature is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_retest_session.py`:

```python
def test_record_verdict_captures_last_command_as_evidence() -> None:
    """The agentic verdict's evidence is the real last command output (Slice 6b-i)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        sid = s.id
        rs.append_event(
            session,
            sid,
            SessionEventKind.COMMAND_OUTPUT,
            {"command": "curl -s http://lab/login", "stdout": "{token}", "stderr": "",
             "exit_code": 0, "elapsed_ms": 12},
        )
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
        [row] = session.scalars(select(VerdictRecord)).all()
        assert row.evidence is not None
        assert row.evidence["explanation"] == "auth still bypassable"
        assert row.evidence["command"] == "curl -s http://lab/login"
        assert row.evidence["output"].startswith("{token}")
        assert row.evidence["exit_code"] == 0
        assert row.evidence["elapsed_ms"] == 12


def test_record_verdict_evidence_is_explanation_only_without_a_command() -> None:
    """A verdict reached with no command run is explanation-only, still valid (Slice 6b-i)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        sid = rs.create_session(session, finding_id=fid, model="m").id
        rs.record_verdict(session, sid, VerdictStatus.INCONCLUSIVE, "cannot tell")
        [row] = session.scalars(select(VerdictRecord)).all()
        assert row.evidence is not None
        assert row.evidence["explanation"] == "cannot tell"
        assert row.evidence["command"] == ""
        assert row.evidence["output"] == ""
        assert row.evidence["exit_code"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_retest_session.py -q -k "captures_last_command or explanation_only"`
Expected: FAIL — `row.evidence` is `None` (record_verdict doesn't build evidence yet).

- [ ] **Step 3: Implement the helpers + wire them in**

In `src/revalid/retest_session.py`, add a module-level constant near the top (after the imports):

```python
# Cap a captured command's output in a verdict's evidence, mirroring the HTTP
# probe body cap (retest._BODY_EXCERPT_LIMIT): a chatty tool must not bloat a row.
_OUTPUT_EXCERPT_LIMIT = 16_384
```

Add these two helpers immediately **above** `def record_verdict(`:

```python
def _last_command_output(session: Session, session_id: int) -> dict[str, Any] | None:
    """Return the session's most recent ``command_output`` payload, or ``None``."""
    rows = session.scalars(
        select(SessionEventRecord)
        .where(
            SessionEventRecord.session_id == session_id,
            SessionEventRecord.kind == SessionEventKind.COMMAND_OUTPUT.value,
        )
        .order_by(SessionEventRecord.seq)
    ).all()
    return dict(rows[-1].payload) if rows else None


def _build_agentic_evidence(
    session: Session, session_id: int, rationale: str
) -> AgenticEvidence:
    """Assemble the agent's verdict proof: its rationale + the real last command output.

    The proof is the *actual* captured output of the decisive command (the last
    one the agent ran), not the model restating it — so it stays consistent with
    the transcript the FR-10 audit checks. Explanation-only when no command ran.
    """
    last = _last_command_output(session, session_id)
    if last is None:
        return AgenticEvidence(explanation=rationale)
    stdout = str(last.get("stdout", ""))
    stderr = str(last.get("stderr", ""))
    output = stdout if not stderr else f"{stdout}\n--- stderr ---\n{stderr}"
    exit_code = last.get("exit_code")
    return AgenticEvidence(
        explanation=rationale,
        command=str(last.get("command", "")),
        output=output[:_OUTPUT_EXCERPT_LIMIT],
        exit_code=exit_code if isinstance(exit_code, int) else None,
        elapsed_ms=float(last.get("elapsed_ms", 0.0)),
    )
```

In `record_verdict`, change the `session.add(VerdictRecord.agentic(...))` call to build and pass the evidence. Replace:

```python
    session.add(
        VerdictRecord.agentic(
            finding_id=record.finding_id,
            session_id=session_id,
            status=status,
            rationale=rationale,
            actor="agent",
            reason_code="agentic_conclusion",
        )
    )
    session.commit()
```

with:

```python
    session.add(
        VerdictRecord.agentic(
            finding_id=record.finding_id,
            session_id=session_id,
            status=status,
            rationale=rationale,
            actor="agent",
            reason_code="agentic_conclusion",
            evidence=_build_agentic_evidence(session, session_id, rationale).model_dump(),
        )
    )
    session.commit()
```

Add the import: in the `from revalid.domain import (...)` block, add `AgenticEvidence` (keep alphabetical). Verify with grep after the edit that the import survived the write hook (re-add if stripped).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_retest_session.py -q && uv run mypy --strict src/revalid/retest_session.py && uv run xenon --max-absolute C src/revalid/retest_session.py`
Expected: PASS; mypy clean; xenon clean (the helpers keep `record_verdict` simple).

- [ ] **Step 5: Update the adjudication path (it must not overwrite the agent's evidence)**

`adjudicate_verdict` already calls `VerdictRecord.agentic(...)` without `evidence` → the operator's superseding record is explanation-free, which is correct (a human override has no captured command). No change needed. Confirm by running:

Run: `uv run pytest tests/unit/test_retest_session.py -q -k adjudicate`
Expected: PASS (existing adjudication tests unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/revalid/retest_session.py tests/unit/test_retest_session.py
git commit -m "feat(retest): capture the decisive command as agentic verdict evidence (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: FR-12 export carries `AgenticEvidence` + schema 1.3

**Files:**
- Modify: `src/revalid/export.py` (`VerdictExport`, `_verdict_export`, `SCHEMA_VERSION`)
- Modify: `tests/unit/test_export.py` (update the 6a agentic test)
- Modify: `docs/reference/schemas/run-export.schema.json` (regenerated)

**Interfaces:**
- Consumes: `AgenticEvidence` (Task 1), `VerdictRecord` rows with an `evidence` dict + `source` (Tasks 2–3).
- Produces: `VerdictExport.evidence: Evidence | AgenticEvidence | None`; `SCHEMA_VERSION == "1.3"`.

- [ ] **Step 1: Update the 6a test to assert populated agentic evidence**

In `tests/unit/test_export.py`, in `test_export_carries_agentic_verdict`, first make the session run a command before concluding, then flip the assertion. Replace the body's setup + assertion:

```python
    sid = rs.create_session(session, finding_id=1, model="m").id
    rs.append_event(
        session,
        sid,
        __import__("revalid.domain", fromlist=["SessionEventKind"]).SessionEventKind.COMMAND_OUTPUT,
        {"command": "curl -s http://lab/x", "stdout": "{token}", "stderr": "",
         "exit_code": 0, "elapsed_ms": 9},
    )
    rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "agent says still open")
```

and replace `assert verdict.evidence is None` with:

```python
    assert verdict.evidence is not None
    assert verdict.evidence.explanation == "agent says still open"
    assert verdict.evidence.command == "curl -s http://lab/x"
```

Also change the metrics assertion `assert export.metrics.total_elapsed_ms == 0.0` to `assert export.metrics.total_elapsed_ms == 9.0` (the captured command's `elapsed_ms` now counts).

(Prefer a clean top-level `from revalid.domain import SessionEventKind` import over the `__import__` form if it isn't already imported — check the file's imports and add it there.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_export.py::test_export_carries_agentic_verdict -q`
Expected: FAIL — `verdict.evidence` is `None` (export builds no agentic evidence yet) / `AttributeError`.

- [ ] **Step 3: Implement the union + branch + schema bump**

In `src/revalid/export.py`:

Change `SCHEMA_VERSION = "1.2"` to `SCHEMA_VERSION = "1.3"` and update its comment to append: `# 1.3: VerdictExport.evidence carries flexible AgenticEvidence for agentic verdicts (FR-17 6b-i).`

Add `AgenticEvidence` to the domain import: `from revalid.domain import AgenticEvidence, Evidence, Finding, Probe, VerdictStatus`.

Change the `VerdictExport.evidence` annotation from `evidence: Evidence | None` to:

```python
    evidence: Evidence | AgenticEvidence | None
```

Update its docstring line to note both shapes.

Change `_verdict_export`'s evidence line. Replace:

```python
        evidence=Evidence(**record.evidence) if record.evidence is not None else None,
```

with:

```python
        evidence=_evidence_export(record),
```

and add this helper immediately above `_verdict_export`:

```python
def _evidence_export(record: VerdictRecord) -> Evidence | AgenticEvidence | None:
    """Build the right evidence shape for a verdict row (batch vs agentic)."""
    if record.evidence is None:
        return None
    if record.source == "agentic":
        return AgenticEvidence(**record.evidence)
    return Evidence(**record.evidence)
```

- [ ] **Step 4: Regenerate the published schema**

Run: `make export-schema`
Expected: writes `docs/reference/schemas/run-export.schema.json`; `git diff --stat` shows it changed (new `AgenticEvidence` definition + `schema_version` const 1.3).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_export.py tests/unit/test_eval.py -q && uv run mypy --strict src/revalid/export.py && uv run ruff check src/revalid/export.py`
Expected: PASS (incl. the drift test `test_published_schema_matches_model`); mypy clean; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/export.py tests/unit/test_export.py docs/reference/schemas/run-export.schema.json
git commit -m "feat(export): carry AgenticEvidence in the run export; schema 1.2 -> 1.3 (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `VerdictOut` carries `AgenticEvidence` (+ integration)

**Files:**
- Modify: `src/revalid/app.py` (`VerdictOut`, `VerdictOut.from_record`)
- Modify: `tests/integration/test_retest_session_api.py` (update the 6a agentic assertion)

**Interfaces:**
- Consumes: `AgenticEvidence` (Task 1); `_evidence_export` pattern (Task 4) mirrored for `VerdictOut`.
- Produces: `VerdictOut.evidence: Evidence | AgenticEvidence | None`.

- [ ] **Step 1: Update the 6a integration test to expect populated evidence**

In `tests/integration/test_retest_session_api.py`, in `test_agentic_verdict_is_queryable_and_adjudicable`, replace `assert verdicts[0]["evidence"] is None` with:

```python
        assert verdicts[0]["evidence"] is not None
        assert verdicts[0]["evidence"]["explanation"]  # the agent's account
```

(In `_client()` the scripted `FunctionModel` runs one command before concluding, so evidence is populated.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_retest_session_api.py::test_agentic_verdict_is_queryable_and_adjudicable -q`
Expected: FAIL — `evidence` is `None` (VerdictOut builds no agentic evidence yet).

- [ ] **Step 3: Implement the union + branch**

In `src/revalid/app.py`:

Ensure `AgenticEvidence` is imported: add it to the `from revalid.domain import (...)` block (keep alphabetical, first entry).

Change `VerdictOut.evidence` from `evidence: Evidence | None` to:

```python
    evidence: Evidence | AgenticEvidence | None
```

In `VerdictOut.from_record`, replace:

```python
            evidence=Evidence(**record.evidence) if record.evidence is not None else None,
```

with:

```python
            evidence=_verdict_out_evidence(record),
```

and add this helper immediately above the `VerdictOut` class (or just above `from_record`'s enclosing class — keep it module-level near `VerdictOut`):

```python
def _verdict_out_evidence(record: VerdictRecord) -> Evidence | AgenticEvidence | None:
    """Build the right evidence shape for the API view (batch vs agentic)."""
    if record.evidence is None:
        return None
    if record.source == "agentic":
        return AgenticEvidence(**record.evidence)
    return Evidence(**record.evidence)
```

Verify with grep that the `AgenticEvidence` import survived the write hook (re-add if stripped).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/integration/test_retest_session_api.py -q && uv run mypy --strict src/revalid/app.py && uv run ruff check src/revalid/app.py && uv run xenon --max-absolute C src/revalid/app.py`
Expected: PASS; mypy clean; ruff clean; xenon clean.

- [ ] **Step 5: Commit**

```bash
git add src/revalid/app.py tests/integration/test_retest_session_api.py
git commit -m "feat(api): VerdictOut carries AgenticEvidence at GET /verdicts (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Frontend — render agentic evidence

**Files:**
- Modify: `frontend/src/api/types.ts` (add `AgenticEvidence`; widen `Verdict.evidence`)
- Modify: `frontend/src/components/EvidenceView.tsx` (branch on shape)
- Modify: `frontend/src/components/EvidenceView.test.tsx` (update the 6a agentic test)

**Interfaces:**
- Consumes: the API's `evidence: Evidence | AgenticEvidence | null` (Task 5).
- Produces: `EvidenceView` renders explanation + command + output for agentic verdicts.

- [ ] **Step 1: Update the 6a EvidenceView test to expect the rendered proof**

In `frontend/src/components/EvidenceView.test.tsx`, replace the `"shows a transcript note ... for an agentic verdict"` test body's `agentic` object and assertions:

```typescript
    const agentic: Verdict = {
      ...verdict,
      source: "agentic",
      session_id: 9,
      actor: "agent",
      evidence: {
        explanation: "login bypass still returns a token",
        command: "curl -s http://lab.local/rest/user/login",
        output: '{"authentication":{"token":"eyJ..."}}',
        exit_code: 0,
        elapsed_ms: 42,
      },
    };
    render(<EvidenceView verdict={agentic} />);

    expect(screen.getByText(/login bypass still returns a token/)).toBeInTheDocument();
    expect(screen.getByText("curl -s http://lab.local/rest/user/login")).toBeInTheDocument();
    expect(screen.getByText('{"authentication":{"token":"eyJ..."}}')).toBeInTheDocument();
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/EvidenceView.test.tsx`
Expected: FAIL — the agentic object doesn't match the `Verdict` type (`evidence` shape) and/or the text isn't rendered.

- [ ] **Step 3: Add the `AgenticEvidence` type**

In `frontend/src/api/types.ts`, add after the `Evidence` interface:

```typescript
export interface AgenticEvidence {
  explanation: string;
  command: string;
  output: string;
  exit_code: number | null;
  elapsed_ms: number;
}
```

and widen the `Verdict.evidence` field:

```typescript
  evidence: Evidence | AgenticEvidence | null;
```

- [ ] **Step 4: Branch the render in `EvidenceView`**

In `frontend/src/components/EvidenceView.tsx`, change the import to include the new type:

```typescript
import type { AgenticEvidence, Verdict } from "../api/types";
```

Replace the null-guard block with a null-guard **and** an agentic branch (put this right after `const { evidence } = verdict;`):

```typescript
  if (evidence === null) {
    return null;
  }
  if ("explanation" in evidence) {
    return <AgenticEvidenceView evidence={evidence} />;
  }
```

Add the `AgenticEvidenceView` component above `EvidenceView` (reusing the existing `Field` + `<details>` chrome):

```typescript
function AgenticEvidenceView({ evidence }: { evidence: AgenticEvidence }) {
  return (
    <details className="group mt-3 overflow-hidden rounded-lg border border-line bg-panel-2/50">
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-dim transition-colors hover:text-fg">
        Evidence
      </summary>
      <dl className="space-y-2.5 border-t border-line px-3 py-3">
        <Field label="Explanation" value={evidence.explanation} />
        {evidence.command && <Field label="Command" value={evidence.command} mono />}
        {evidence.output && <Field label="Output" value={evidence.output} mono />}
        {evidence.exit_code !== null && (
          <Field label="Exit code" value={String(evidence.exit_code)} />
        )}
        <Field label="Elapsed" value={`${String(evidence.elapsed_ms)} ms`} />
      </dl>
    </details>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/EvidenceView.test.tsx && npx tsc --noEmit && npx eslint src/components/EvidenceView.tsx src/api/types.ts`
Expected: PASS; tsc clean; eslint clean.

- [ ] **Step 6: Update any other `Verdict` fixtures the wider type breaks**

Run: `cd frontend && npx tsc --noEmit`
If any test fixture (e.g. `selectors.test.ts`, `stages.test.tsx`) now fails because `evidence` needs the wider type — they already set an HTTP `Evidence`, which still satisfies the union, so no change is expected. Fix only what tsc flags.

- [ ] **Step 7: Full frontend gate + commit**

Run: `cd frontend && npx vitest run --coverage && npm run build`
Expected: all tests pass; coverage floor met; build succeeds.

```bash
git add frontend/src/api/types.ts frontend/src/components/EvidenceView.tsx frontend/src/components/EvidenceView.test.tsx
git commit -m "feat(ui): render flexible agentic evidence in EvidenceView (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Docs, ADR-0031, SRS, roadmap + full verification + PR

**Files:**
- Create: `docs/adr/0031-agentic-evidence.md`; Modify: `docs/adr/README.md`, `docs/requirements/srs.md`, `docs/roadmap.md`

- [ ] **Step 1:** Write ADR-0031 (proposed) via the `adr` skill (or match ADR-0030's format): decision = repurpose the evidence slot with a flexible, tool-agnostic `AgenticEvidence` captured from the transcript; alternatives = keep evidence-free (6a), force HTTP `Evidence` (rejected — agent runs arbitrary tools), LLM restates evidence (rejected — capture real data). Add the row to `docs/adr/README.md`.

- [ ] **Step 2:** Add SRS FR-17 acceptance criteria for 6b-i (an agentic verdict carries flexible explanation+command-output evidence, captured from the transcript, in `/verdicts` + export + UI; schema 1.2 → 1.3).

- [ ] **Step 3:** Add a roadmap entry + note 6b split into 6b-i/6b-ii/6b-iii under the M6 Slice 6 list.

- [ ] **Step 4: Full gate**

Run: `uv run pytest tests/unit tests/integration -q && uv run pytest --cov=src/revalid --cov-report=term-missing -q && uv run mypy --strict src tests && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run xenon --max-absolute C src`
Expected: all green; coverage ≥ 80% on `src/`.

Run: `cd frontend && npx tsc --noEmit && npx eslint src && npx vitest run --coverage && npm run build`
Expected: all green.

Run: `make demo-retest-session && make demo-export`
Expected: both succeed; the export validates against the 1.3 schema.

- [ ] **Step 5: Commit docs + open the PR**

```bash
git add docs/
git commit -m "docs(retest): ADR-0031 + SRS/roadmap for agentic evidence (FR-17 6b-i)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git push -u origin feat/fr17-agentic-evidence-slice6b-i
```

Open the PR with a filled "How to validate" section and `Closes #104`; queue squash auto-merge; monitor CI to green.

---

## Self-Review (completed during authoring)

- **Spec coverage:** §2 `AgenticEvidence` → Task 1; §3 capture from transcript → Task 3; §4 db/export/app threading + schema 1.3 → Tasks 2/4/5; §5 frontend → Task 6; §7 acceptance + §8 test-updates → covered in Tasks 3–6 + Task 7. No gaps.
- **Placeholder scan:** no TBD/TODO; every code step shows the code.
- **Type consistency:** `AgenticEvidence(explanation, command, output, exit_code, elapsed_ms)` used identically in Tasks 1/3/4/5/6; `_evidence_export`/`_verdict_out_evidence` mirror each other; `record_verdict` signature unchanged (evidence built internally).
