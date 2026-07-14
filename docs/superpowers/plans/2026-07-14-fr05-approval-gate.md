# FR-05 Approval Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-side plan review/approval gate so no retest plan executes without an approval, with edits/regeneration versioned and the executed version recorded.

**Architecture:** A new `plans` table stores one immutable row per plan version. A new `approval.py` service owns the state machine (propose → approve/reject/supersede) and the *single* execution chokepoint `execute_approved_plan`, which refuses anything without an `approved` row (AC1). Editing reuses FR-04's allowlist gate so edited actions can't escape FR-06. `POST /findings/{id}/retest` is rewired to run the approved plan's probes and stamp each verdict with the executed version (AC2).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (SQLite), Pydantic v2, Pydantic AI (TestModel/FunctionModel in tests), httpx (`MockTransport` in tests), pytest.

## Global Constraints

- Python 3.12+, managed with `uv`; run tools via `uv run` / `make`.
- `mypy --strict` must pass; Ruff lint + format, line length 100, Google-style docstrings on public API.
- Complexity gate: xenon max absolute **C** — refactor, never suppress.
- Tests by pyramid level: `tests/unit/` (no I/O; LLM via Pydantic AI `TestModel`/`FunctionModel`), `tests/integration/` (marker `integration`, real wiring, LLM via stand-ins), `tests/system/` (marker `system`, dockerized lab). Coverage ≥ **80%** on `src/`.
- The app binds 127.0.0.1 only (NFR-03); no auth in scope — the audit actor is the fixed token `"user"`.
- Every commit carries `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and a Conventional-Commit subject.
- Reuse before adding: edited/generated actions share **one** gate (`plan.gate_actions`); do not write a second allowlist check.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/revalid/plan.py` | Modify | Extract public `gate_actions`; `generate_plan` reuses it. |
| `src/revalid/retest.py` | Modify | Kind-dispatched assessment: `assess_generic` + `_ASSESSORS` registry; `run_probe` dispatches. |
| `src/revalid/domain.py` | Modify | Add `PlanStatus` StrEnum. |
| `src/revalid/db.py` | Modify | Add `PlanRecord`; stamp `VerdictRecord` with `plan_id`/`plan_version`. |
| `src/revalid/approval.py` | Create | Approval state machine + `execute_approved_plan` chokepoint + error types. |
| `src/revalid/app.py` | Modify | Plan endpoints, `PlanOut`, `get_plan_agent` dep, rewired `retest`. |
| `tests/unit/test_plan.py` | Modify | Add a `gate_actions` direct check (existing tests stay green). |
| `tests/unit/test_retest.py` | Modify | Add `assess_generic` + unknown-kind dispatch tests. |
| `tests/unit/test_db_plan.py` | Create | `PlanRecord` round-trip; verdict stamp columns. |
| `tests/unit/test_approval.py` | Create | State-machine transitions (Task 4). |
| `tests/unit/test_approval_execute.py` | Create | Chokepoint AC1 refusal + version stamp AC2 (Task 5). |
| `tests/unit/test_retest_api.py` | Modify | Rewrite for the approved-plan retest contract (list + 409). |
| `tests/integration/test_approval_api.py` | Create | Full HTTP flow + AC1 negative + versioning (AC2). |
| `tests/system/test_retest_system.py` | Modify | Add app-path seed→approve→retest against the live lab. |
| `scripts/demo/approval_gate.py` | Create | `make demo-approval`: refused → approve → retest; edit → v2. |
| `Makefile` | Modify | `demo-approval` target. |
| `docs/adr/0012-*.md` | Create | ADR-0012 (proposed). |
| `docs/roadmap.md`, `docs/requirements/srs.md` | Modify | Tick FR-05; update current-state + next action. |

---

### Task 1: Extract the reusable action gate (`gate_actions`)

**Files:**
- Modify: `src/revalid/plan.py`
- Test: `tests/unit/test_plan.py`

**Interfaces:**
- Consumes: `PlannedAction`, `RejectedAction`, `Probe`, `TargetGuard`, existing `_gate`.
- Produces: `gate_actions(actions: Iterable[PlannedAction], guard: TargetGuard, base_url: str) -> tuple[list[Probe], list[RejectedAction]]` (Task 4 reuses it).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_plan.py`:

```python
def test_gate_actions_splits_survivors_from_rejects() -> None:
    from revalid.plan import PlannedAction, gate_actions

    ok = PlannedAction(**_LOGIN_ACTION)
    bad = PlannedAction(**{**_LOGIN_ACTION, "target": "http://evil.example/"})
    probes, rejected = gate_actions([ok, bad], _GUARD, _BASE_URL)

    assert [p.url for p in probes] == ["http://localhost:3000/rest/user/login"]
    assert [r.reason for r in rejected] == ["not_allowlisted"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plan.py::test_gate_actions_splits_survivors_from_rejects -v`
Expected: FAIL with `ImportError: cannot import name 'gate_actions'`.

- [ ] **Step 3: Implement `gate_actions` and refactor `generate_plan`**

In `src/revalid/plan.py`, add `Iterable` to the typing import:

```python
from collections.abc import Iterable
```

Add the public function just above `generate_plan`:

```python
def gate_actions(
    actions: Iterable[PlannedAction], guard: TargetGuard, base_url: str
) -> tuple[list[Probe], list[RejectedAction]]:
    """Split proposed actions into gated probes and audited rejections (FR-04/FR-06).

    The single allowlist/method gate for both model-generated (FR-04) and
    user-edited (FR-05) actions: each action is resolved against ``base_url`` and
    checked against ``guard``; only non-destructive methods on allowlisted targets
    survive. Dropped actions are returned with a machine-readable reason.

    Args:
        actions: The proposed actions to gate.
        guard: The FR-06 allowlist guard — the sole authority on allowed targets.
        base_url: Allowlisted base URL that relative targets resolve against.

    Returns:
        A ``(probes, rejected)`` pair: runnable probes and audited rejections.
    """
    probes: list[Probe] = []
    rejected: list[RejectedAction] = []
    for item in actions:
        outcome = _gate(item, guard, base_url)
        if isinstance(outcome, Probe):
            probes.append(outcome)
        else:
            rejected.append(RejectedAction(action=item, reason=outcome))
    return probes, rejected
```

Replace the gating loop inside `generate_plan` (the `for item in proposed:` block and the two lists) with:

```python
    actions, rejected = gate_actions(proposed, guard, base_url)
```

Leave the `RetestPlan(...)` / `PlanResult(...)` construction below it unchanged (it already reads `actions` and `rejected`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_plan.py -v`
Expected: PASS (new test + all existing FR-04 tests — behaviour is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/revalid/plan.py tests/unit/test_plan.py
git commit -m "refactor(plan): extract reusable gate_actions for FR-05 reuse (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Kind-dispatched probe assessment

**Files:**
- Modify: `src/revalid/retest.py`
- Test: `tests/unit/test_retest.py`

**Interfaces:**
- Consumes: `Evidence`, `Verdict`, `VerdictStatus`, existing `assess`, `execute`, `_unreachable_verdict`.
- Produces: `assess_generic(evidence: Evidence) -> Verdict`; `run_probe` now dispatches by `probe.kind` (Task 5 relies on this for `planned-http` probes).

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_retest.py`:

```python
def test_assess_generic_is_inconclusive_with_no_assessor_reason() -> None:
    from revalid.retest import assess_generic

    verdict = assess_generic(_evidence(200, "irrelevant"))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.reason_code == "no_assessor"
    assert "http_200" in verdict.matched_indicators


def test_run_probe_dispatches_unknown_kind_to_generic() -> None:
    from revalid.domain import Probe

    probe = Probe(kind="planned-http", method="GET", url="http://localhost:3000/rest/x")
    verdict = run_probe(_client(lambda _r: httpx.Response(200, text="ok")), probe)
    assert verdict.reason_code == "no_assessor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_retest.py -k "generic" -v`
Expected: FAIL with `ImportError: cannot import name 'assess_generic'`.

- [ ] **Step 3: Implement dispatch in `src/revalid/retest.py`**

Add to the imports at the top:

```python
from collections.abc import Callable
```

Add `assess_generic` immediately after the existing `assess` function:

```python
def assess_generic(evidence: Evidence) -> Verdict:
    """Assess a probe with no kind-specific matcher (FR-05 execution).

    Without a bespoke matcher every outcome is honestly *inconclusive* — generic
    indicator-matching from ``expected_indicator`` is FR-08/FR-09 work, not
    guessed here. The observed status is recorded for the audit trail.
    """
    status = evidence.response_status
    return Verdict(
        status=VerdictStatus.INCONCLUSIVE,
        reason_code="no_assessor",
        rationale=(
            f"No kind-specific assessor for this probe; observed HTTP {status}. "
            "Manual review required (generic matching is FR-08/FR-09)."
        ),
        matched_indicators=(f"http_{status}",),
        evidence=evidence,
    )


# Assessors keyed by probe kind; unknown kinds fall back to assess_generic.
_ASSESSORS: dict[str, Callable[[Evidence], Verdict]] = {"sqli-login-bypass": assess}
```

Change the last line of `run_probe` from `return assess(evidence)` to:

```python
    return _ASSESSORS.get(probe.kind, assess_generic)(evidence)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_retest.py -v`
Expected: PASS (new tests + all existing — the SQLi probe still routes to `assess`).

- [ ] **Step 5: Commit**

```bash
git add src/revalid/retest.py tests/unit/test_retest.py
git commit -m "feat(retest): dispatch verdict assessment by probe kind (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Persistence — `PlanStatus`, `PlanRecord`, verdict stamp

**Files:**
- Modify: `src/revalid/domain.py`, `src/revalid/db.py`
- Test: `tests/unit/test_db_plan.py` (create)

**Interfaces:**
- Consumes: `RetestPlan`, `Probe`, `Verdict`, `Evidence` (domain).
- Produces:
  - `domain.PlanStatus` (StrEnum: `PROPOSED`/`APPROVED`/`REJECTED`/`SUPERSEDED`).
  - `db.PlanRecord` with `from_plan(finding_id: int, plan: RetestPlan, *, version: int, status: PlanStatus, origin: str, rejected_actions: list[dict[str, Any]]) -> PlanRecord` and `probes() -> tuple[Probe, ...]`.
  - `db.VerdictRecord.from_domain(finding_id, probe_kind, verdict, *, plan_id: int | None = None, plan_version: int | None = None)` and the columns `plan_id`, `plan_version`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/test_db_plan.py`:

```python
"""Unit tests for plan persistence and the verdict-version stamp (FR-05)."""

from sqlalchemy.orm import Session

from revalid.db import IN_MEMORY, PlanRecord, VerdictRecord, create_db_engine, session_factory
from revalid.domain import (
    Evidence,
    PlanStatus,
    Probe,
    RetestPlan,
    Verdict,
    VerdictStatus,
)


def _session() -> Session:
    return session_factory(create_db_engine(IN_MEMORY))()


def _plan() -> RetestPlan:
    probe = Probe(kind="planned-http", method="GET", url="http://localhost:3000/rest/x")
    return RetestPlan(finding_title="F", actions=(probe,), raw={"finding_title": "F"})


def test_plan_record_roundtrips_actions_and_status() -> None:
    with _session() as session:
        record = PlanRecord.from_plan(
            1, _plan(), version=2, status=PlanStatus.PROPOSED, origin="edited", rejected_actions=[]
        )
        session.add(record)
        session.commit()
        session.refresh(record)

        assert record.version == 2
        assert record.status == "proposed"
        assert record.origin == "edited"
        [probe] = record.probes()
        assert probe.url == "http://localhost:3000/rest/x"
        assert record.created_at is not None


def test_verdict_record_stamps_plan_version() -> None:
    verdict = Verdict(
        status=VerdictStatus.STILL_OPEN,
        reason_code="x",
        evidence=Evidence(request_method="GET", request_url="u", response_status=200),
    )
    record = VerdictRecord.from_domain(1, "planned-http", verdict, plan_id=7, plan_version=3)
    assert record.plan_id == 7
    assert record.plan_version == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_db_plan.py -v`
Expected: FAIL with `ImportError: cannot import name 'PlanStatus'`.

- [ ] **Step 3a: Add `PlanStatus` to `src/revalid/domain.py`**

After the `RetestPlan` class (before `Evidence`), add:

```python
class PlanStatus(enum.StrEnum):
    """Lifecycle state of a persisted retest-plan version (FR-05)."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
```

- [ ] **Step 3b: Add `PlanRecord` and the verdict stamp to `src/revalid/db.py`**

Extend the SQLAlchemy imports:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, Engine, ForeignKey, String, create_engine, func
```

Extend the domain import:

```python
from revalid.domain import Evidence, Finding, PlanStatus, Probe, RetestPlan, Severity, Verdict, VerdictStatus
```

Add `PlanRecord` after `FindingRecord`:

```python
class PlanRecord(Base):
    """One immutable version of a retest plan for a finding (FR-05).

    Each edit or regeneration inserts a new row (``version`` bumped); a row is
    only ever mutated to record its own decision or to be marked ``superseded``.
    The approval fields (``status``/``decided_at``/``decided_by``) are the
    minimal audit of the review event (FR-10 later unifies the full trail).
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(16))
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    rejected_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    decided_by: Mapped[str | None] = mapped_column(String(32), default=None)

    @classmethod
    def from_plan(
        cls,
        finding_id: int,
        plan: RetestPlan,
        *,
        version: int,
        status: PlanStatus,
        origin: str,
        rejected_actions: list[dict[str, Any]],
    ) -> PlanRecord:
        """Build a proposed/decided plan row from a domain plan."""
        return cls(
            finding_id=finding_id,
            version=version,
            status=status.value,
            origin=origin,
            actions=[p.model_dump() for p in plan.actions],
            rejected_actions=rejected_actions,
            raw=plan.raw,
        )

    def probes(self) -> tuple[Probe, ...]:
        """Rehydrate the stored actions as runnable probes."""
        return tuple(Probe(**action) for action in self.actions)
```

Add the two stamp columns to `VerdictRecord` (after `evidence`):

```python
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), default=None)
    plan_version: Mapped[int | None] = mapped_column(default=None)
```

Update `VerdictRecord.from_domain` to accept and set them:

```python
    @classmethod
    def from_domain(
        cls,
        finding_id: int,
        probe_kind: str,
        verdict: Verdict,
        *,
        plan_id: int | None = None,
        plan_version: int | None = None,
    ) -> VerdictRecord:
        """Build a row from a domain verdict against ``finding_id``."""
        return cls(
            finding_id=finding_id,
            probe_kind=probe_kind,
            status=verdict.status.value,
            reason_code=verdict.reason_code,
            rationale=verdict.rationale,
            matched_indicators=list(verdict.matched_indicators),
            evidence=verdict.evidence.model_dump(),
            plan_id=plan_id,
            plan_version=plan_version,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_db_plan.py -v`
Expected: PASS. Also run `uv run pytest tests/unit/test_retest_api.py -k verdict -v` — still PASS (defaults keep old callers valid).

- [ ] **Step 5: Commit**

```bash
git add src/revalid/domain.py src/revalid/db.py tests/unit/test_db_plan.py
git commit -m "feat(db): PlanRecord + PlanStatus and verdict version stamp (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Approval state machine (`approval.py`)

**Files:**
- Create: `src/revalid/approval.py`
- Test: `tests/unit/test_approval.py` (create)

**Interfaces:**
- Consumes: `PlanRecord`, `FindingRecord` (db); `PlanStatus`, `RetestPlan` (domain); `PlannedAction`, `RejectedAction`, `PlanResult`, `gate_actions` (plan); `TargetGuard` (allowlist).
- Produces (Task 5 & 6 rely on these):
  - `save_generated_plan(session, finding_id, result: PlanResult) -> PlanRecord`
  - `edit_plan(session, finding_id, actions: list[PlannedAction], guard: TargetGuard, base_url: str) -> tuple[PlanRecord, list[RejectedAction]]`
  - `approve_plan(session, finding_id, actor: str = "user") -> PlanRecord`
  - `reject_plan(session, finding_id, actor: str = "user") -> PlanRecord`
  - `approved_plan(session, finding_id) -> PlanRecord | None`
  - `list_plans(session, finding_id) -> list[PlanRecord]`
  - Errors: `NoProposedPlanError(finding_id)`, `AllActionsRejectedError(finding_id, rejected)`, `PlanNotApprovedError(finding_id)`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_approval.py`:

```python
"""Unit tests for the FR-05 approval state machine (no network)."""

import pytest
from sqlalchemy.orm import Session

from revalid.allowlist import TargetGuard
from revalid.approval import (
    AllActionsRejectedError,
    NoProposedPlanError,
    approve_plan,
    approved_plan,
    edit_plan,
    list_plans,
    reject_plan,
    save_generated_plan,
)
from revalid.db import IN_MEMORY, FindingRecord, create_db_engine, session_factory
from revalid.domain import Finding, PlanStatus, Probe, RetestPlan, Severity
from revalid.plan import PlanResult, PlannedAction

_GUARD = TargetGuard(frozenset({"http://localhost:3000/*"}))
_BASE_URL = "http://localhost:3000"

_ACTION = PlannedAction(
    method="POST",
    target="/rest/user/login",
    headers={"Content-Type": "application/json"},
    json_body={"email": "' OR 1=1--", "password": "x"},
    expected_indicator="HTTP 200 with a token means still open.",
)


def _session() -> Session:
    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(FindingRecord.from_domain(Finding(title="F", severity=Severity.HIGH)))
    session.commit()
    return session


def _generated() -> PlanResult:
    probe = Probe(kind="planned-http", method="GET", url="http://localhost:3000/rest/x")
    plan = RetestPlan(finding_title="F", actions=(probe,), raw={"finding_title": "F"})
    return PlanResult(plan=plan)


def test_generate_creates_proposed_v1() -> None:
    with _session() as session:
        record = save_generated_plan(session, 1, _generated())
        assert record.version == 1
        assert record.status == PlanStatus.PROPOSED.value
        assert record.origin == "generated"


def test_approve_marks_approved_and_records_actor() -> None:
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        approved = approve_plan(session, 1)
        assert approved.status == PlanStatus.APPROVED.value
        assert approved.decided_by == "user"
        assert approved.decided_at is not None
        assert approved_plan(session, 1).id == approved.id


def test_reject_marks_rejected() -> None:
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        assert reject_plan(session, 1).status == PlanStatus.REJECTED.value
        assert approved_plan(session, 1) is None


def test_approve_without_proposal_raises() -> None:
    with _session() as session:
        with pytest.raises(NoProposedPlanError):
            approve_plan(session, 1)


def test_edit_supersedes_prior_proposed_and_bumps_version() -> None:
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        record, rejected = edit_plan(session, 1, [_ACTION], _GUARD, _BASE_URL, finding_title="F")
        assert record.version == 2
        assert record.origin == "edited"
        assert rejected == []
        statuses = {p.version: p.status for p in list_plans(session, 1)}
        assert statuses == {1: PlanStatus.SUPERSEDED.value, 2: PlanStatus.PROPOSED.value}


def test_approving_new_version_supersedes_prior_approved() -> None:
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        approve_plan(session, 1)
        edit_plan(session, 1, [_ACTION], _GUARD, _BASE_URL, finding_title="F")
        approve_plan(session, 1)
        statuses = {p.version: p.status for p in list_plans(session, 1)}
        assert statuses == {1: PlanStatus.SUPERSEDED.value, 2: PlanStatus.APPROVED.value}


def test_edit_with_all_actions_off_allowlist_raises() -> None:
    off = PlannedAction(
        method="GET", target="http://evil.example/", expected_indicator="x"
    )
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        with pytest.raises(AllActionsRejectedError):
            edit_plan(session, 1, [off], _GUARD, _BASE_URL, finding_title="F")
        # nothing persisted: v1 remains the only (still proposed) row
        assert [p.version for p in list_plans(session, 1)] == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_approval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'revalid.approval'`.

- [ ] **Step 3: Create `src/revalid/approval.py`**

```python
"""Server-side retest-plan approval gate and versioning (FR-05, ADR-0012).

Plans are inert until approved. This module owns the plan lifecycle — propose
(from FR-04 generation or a user edit) → approve / reject, with older versions
superseded — and the *single* execution chokepoint. Nothing runs a plan except
:func:`execute_approved_plan`, which refuses unless the finding has an
``approved`` version (FR-05 AC1). Edited actions are re-gated through the same
FR-06 allowlist gate as generated ones, so an edit cannot escape the allowlist.
"""

from __future__ import annotations

import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.allowlist import TargetGuard
from revalid.db import PlanRecord, VerdictRecord
from revalid.domain import PlanStatus, RetestPlan
from revalid.plan import PlanResult, PlannedAction, RejectedAction, gate_actions
from revalid.retest import run_probe

_ACTOR = "user"


class NoProposedPlanError(Exception):
    """Raised when approve/reject is attempted with no proposed plan."""

    def __init__(self, finding_id: int) -> None:
        super().__init__(f"finding {finding_id} has no proposed plan")
        self.finding_id = finding_id


class AllActionsRejectedError(Exception):
    """Raised when every edited action is dropped by the gate (nothing to run)."""

    def __init__(self, finding_id: int, rejected: list[RejectedAction]) -> None:
        super().__init__(f"all edited actions for finding {finding_id} were rejected")
        self.finding_id = finding_id
        self.rejected = rejected


class PlanNotApprovedError(Exception):
    """Raised when execution is attempted without an approved plan (AC1)."""

    def __init__(self, finding_id: int) -> None:
        super().__init__(f"finding {finding_id} has no approved plan")
        self.finding_id = finding_id


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _proposed_rows(session: Session, finding_id: int) -> list[PlanRecord]:
    return list(
        session.scalars(
            select(PlanRecord).where(
                PlanRecord.finding_id == finding_id,
                PlanRecord.status == PlanStatus.PROPOSED.value,
            )
        )
    )


def _next_version(session: Session, finding_id: int) -> int:
    versions = session.scalars(
        select(PlanRecord.version).where(PlanRecord.finding_id == finding_id)
    ).all()
    return max(versions) + 1 if versions else 1


def _persist_proposed(
    session: Session,
    finding_id: int,
    plan: RetestPlan,
    origin: str,
    rejected: list[RejectedAction],
) -> PlanRecord:
    """Supersede any live proposal and insert a new proposed version."""
    for row in _proposed_rows(session, finding_id):
        row.status = PlanStatus.SUPERSEDED.value
    record = PlanRecord.from_plan(
        finding_id,
        plan,
        version=_next_version(session, finding_id),
        status=PlanStatus.PROPOSED,
        origin=origin,
        rejected_actions=[r.model_dump() for r in rejected],
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def save_generated_plan(session: Session, finding_id: int, result: PlanResult) -> PlanRecord:
    """Persist an FR-04 generation result as a new proposed version."""
    return _persist_proposed(session, finding_id, result.plan, "generated", list(result.rejected))


def edit_plan(
    session: Session,
    finding_id: int,
    actions: list[PlannedAction],
    guard: TargetGuard,
    base_url: str,
    *,
    finding_title: str,
) -> tuple[PlanRecord, list[RejectedAction]]:
    """Re-gate user-edited actions and persist them as a new proposed version.

    ``finding_title`` is supplied by the caller (the endpoint already loaded the
    finding for its 404 check), keeping this function free of the existence check.

    Raises:
        AllActionsRejectedError: If no submitted action survives the gate.
    """
    probes, rejected = gate_actions(actions, guard, base_url)
    if not probes:
        raise AllActionsRejectedError(finding_id, rejected)
    plan = RetestPlan(
        finding_title=finding_title,
        actions=tuple(probes),
        raw={
            "source": "plan_edit",
            "base_url": base_url,
            "finding_title": finding_title,
            "proposed": len(actions),
            "rejected": len(rejected),
        },
    )
    return _persist_proposed(session, finding_id, plan, "edited", rejected), rejected


def approve_plan(session: Session, finding_id: int, actor: str = _ACTOR) -> PlanRecord:
    """Approve the latest proposed version, superseding any prior approved one."""
    proposed = _latest_proposed(session, finding_id)
    if proposed is None:
        raise NoProposedPlanError(finding_id)
    current = approved_plan(session, finding_id)
    if current is not None:
        current.status = PlanStatus.SUPERSEDED.value
    proposed.status = PlanStatus.APPROVED.value
    proposed.decided_at = _now()
    proposed.decided_by = actor
    session.commit()
    session.refresh(proposed)
    return proposed


def reject_plan(session: Session, finding_id: int, actor: str = _ACTOR) -> PlanRecord:
    """Reject the latest proposed version."""
    proposed = _latest_proposed(session, finding_id)
    if proposed is None:
        raise NoProposedPlanError(finding_id)
    proposed.status = PlanStatus.REJECTED.value
    proposed.decided_at = _now()
    proposed.decided_by = actor
    session.commit()
    session.refresh(proposed)
    return proposed


def _latest_proposed(session: Session, finding_id: int) -> PlanRecord | None:
    return session.scalars(
        select(PlanRecord)
        .where(
            PlanRecord.finding_id == finding_id,
            PlanRecord.status == PlanStatus.PROPOSED.value,
        )
        .order_by(PlanRecord.version.desc())
    ).first()


def approved_plan(session: Session, finding_id: int) -> PlanRecord | None:
    """Return the single approved plan version for a finding, or ``None``."""
    return session.scalars(
        select(PlanRecord).where(
            PlanRecord.finding_id == finding_id,
            PlanRecord.status == PlanStatus.APPROVED.value,
        )
    ).first()


def list_plans(session: Session, finding_id: int) -> list[PlanRecord]:
    """Return all plan versions for a finding, oldest first."""
    return list(
        session.scalars(
            select(PlanRecord)
            .where(PlanRecord.finding_id == finding_id)
            .order_by(PlanRecord.version)
        )
    )


def execute_approved_plan(
    session: Session, client: httpx.Client, finding_id: int
) -> list[VerdictRecord]:
    """Run the approved plan's probes; the ONLY path from storage to the network.

    Raises:
        PlanNotApprovedError: If the finding has no approved plan version (AC1).
    """
    plan = approved_plan(session, finding_id)
    if plan is None:
        raise PlanNotApprovedError(finding_id)
    records: list[VerdictRecord] = []
    for probe in plan.probes():
        verdict = run_probe(client, probe)
        records.append(
            VerdictRecord.from_domain(
                finding_id, probe.kind, verdict, plan_id=plan.id, plan_version=plan.version
            )
        )
    session.add_all(records)
    session.commit()
    for record in records:
        session.refresh(record)
    return records
```

> Note: `execute_approved_plan` is tested in Task 5; Task 4's tests exercise the state machine only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_approval.py -v`
Expected: PASS (all state-machine tests).

- [ ] **Step 5: Commit**

```bash
git add src/revalid/approval.py tests/unit/test_approval.py
git commit -m "feat(approval): FR-05 plan versioning + approve/reject state machine (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Execution chokepoint (AC1 + version stamp)

**Files:**
- Modify: `src/revalid/approval.py` (already contains `execute_approved_plan` from Task 4 — this task proves it)
- Test: `tests/unit/test_approval_execute.py` (create — a separate file keeps the module-level imports at the top and gives this AC its own reviewer gate)

**Interfaces:**
- Consumes: `execute_approved_plan`, `save_generated_plan`, `approve_plan` (Task 4); `httpx.MockTransport`.
- Produces: verified AC1 refusal + AC2 stamp behaviour that Task 6's endpoint relies on.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_approval_execute.py`:

```python
"""Unit tests for the FR-05 execution chokepoint: no run without approval (AC1)."""

from collections.abc import Callable

import httpx
import pytest
from sqlalchemy.orm import Session

from revalid.approval import (
    PlanNotApprovedError,
    approve_plan,
    execute_approved_plan,
    save_generated_plan,
)
from revalid.db import IN_MEMORY, FindingRecord, create_db_engine, session_factory
from revalid.domain import Finding, Probe, RetestPlan, Severity, VerdictStatus
from revalid.plan import PlanResult

Handler = Callable[[httpx.Request], httpx.Response]


def _session() -> Session:
    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(FindingRecord.from_domain(Finding(title="F", severity=Severity.HIGH)))
    session.commit()
    return session


def _probe_client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _sqli_generated() -> PlanResult:
    probe = Probe(
        kind="sqli-login-bypass",
        method="POST",
        url="http://localhost:3000/rest/user/login",
        json_body={"email": "' OR 1=1--", "password": "x"},
    )
    plan = RetestPlan(finding_title="F", actions=(probe,), raw={"finding_title": "F"})
    return PlanResult(plan=plan)


def test_execute_refuses_without_approval() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with _session() as session:
        save_generated_plan(session, 1, _sqli_generated())  # proposed, not approved
        with pytest.raises(PlanNotApprovedError):
            execute_approved_plan(session, _probe_client(handler), 1)
        assert calls == []  # AC1: no socket opened for an unapproved plan


def test_execute_runs_approved_plan_and_stamps_version() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with _session() as session:
        save_generated_plan(session, 1, _sqli_generated())
        approve_plan(session, 1)
        [verdict] = execute_approved_plan(session, _probe_client(handler), 1)
        assert verdict.status == VerdictStatus.STILL_OPEN.value
        assert verdict.plan_version == 1  # AC2: executed version recorded
        assert verdict.finding_id == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_approval_execute.py -v`
Expected: PASS (the implementation shipped in Task 4). If any fails, fix `execute_approved_plan` before continuing — do not proceed on red.

- [ ] **Step 3: Verify the whole unit suite + types + lint**

Run:
```bash
uv run pytest tests/unit -q
uv run mypy --strict src tests
uv run ruff check src tests
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_approval_execute.py
git commit -m "test(approval): AC1 execution refusal + AC2 version stamp (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: API endpoints + rewired retest

**Files:**
- Modify: `src/revalid/app.py`
- Test: `tests/unit/test_retest_api.py` (rewrite), `tests/integration/test_approval_api.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–5; `build_plan_agent`, `generate_plan`, `PlannedAction`, `RejectedAction` (plan); `load_allowlist` (allowlist); `lab_base_url` (retest).
- Produces: `PlanOut` (Pydantic model) with `PlanOut.from_record`; `get_plan_agent` dependency; endpoints `POST/PUT /findings/{id}/plan`, `POST /findings/{id}/plan/approve|reject`, `GET /findings/{id}/plans`, rewired `POST /findings/{id}/retest -> list[VerdictOut]`.

- [ ] **Step 1: Write the failing integration test** — create `tests/integration/test_approval_api.py`:

```python
"""Integration test for the FR-05 approval + retest HTTP flow (no network).

The plan agent is overridden with a FunctionModel; the probe client is a
MockTransport. Proves: no execution without approval (AC1), and edits are
versioned with the executed version stamped (AC2). A *generated* action becomes a
``planned-http`` probe, which assesses as ``inconclusive``/``no_assessor`` (generic
matching is FR-08/FR-09) — the still-open verdict for the login probe is proven in
``tests/unit/test_approval_execute.py`` and the live-lab system test.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_plan_agent, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine
from revalid.plan import PlannedAction, build_plan_agent

pytestmark = pytest.mark.integration

_IMPORT: dict[str, Any] = {
    "scan_type": "Manual pentest",
    "findings": [
        {
            "title": "SQL injection auth bypass in login",
            "severity": "Critical",
            "endpoints": ["http://localhost:3000/rest/user/login"],
            "steps_to_reproduce": "1. POST ' OR 1=1--",
        }
    ],
}

_SQLI_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with an authentication token means still open.",
}


def _agent_proposing(*actions: dict[str, Any]) -> Agent[None, list[PlannedAction]]:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args={"response": list(actions)})])

    return build_plan_agent(FunctionModel(respond))


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))

    def probe_override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = probe_override
    app.dependency_overrides[get_plan_agent] = lambda: _agent_proposing(_SQLI_ACTION)
    return TestClient(app)


def _token(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def test_retest_refused_until_approved_then_executes() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)

        # AC1: no approved plan -> 409, and nothing executes.
        assert client.post("/findings/1/retest").status_code == 409

        assert client.post("/findings/1/plan").json()["status"] == "proposed"
        assert client.post("/findings/1/plan/approve").json()["status"] == "approved"

        verdicts = client.post("/findings/1/retest").json()
        # A generated planned-http probe assesses as inconclusive (FR-08/09 later),
        # but the chokepoint ran it and stamped the executed version (AC2).
        assert [v["status"] for v in verdicts] == ["inconclusive"]
        assert verdicts[0]["reason_code"] == "no_assessor"
        assert verdicts[0]["plan_version"] == 1


def test_edit_creates_v2_and_execution_uses_it() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        client.post("/findings/1/plan")

        edited = client.put("/findings/1/plan", json=[_SQLI_ACTION]).json()
        assert edited["version"] == 2 and edited["origin"] == "edited"

        plans = client.get("/findings/1/plans").json()
        assert {p["version"]: p["status"] for p in plans} == {1: "superseded", 2: "proposed"}

        client.post("/findings/1/plan/approve")
        assert client.post("/findings/1/retest").json()[0]["plan_version"] == 2


def test_edit_all_off_allowlist_is_422() -> None:
    off = {"method": "GET", "target": "http://evil.example/", "expected_indicator": "x"}
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        client.post("/findings/1/plan")
        assert client.put("/findings/1/plan", json=[off]).status_code == 422


def test_approve_without_plan_is_409() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        assert client.post("/findings/1/plan/approve").status_code == 409


def test_reject_blocks_execution() -> None:
    with _client(_token) as client:
        client.post("/findings/import", json=_IMPORT)
        client.post("/findings/1/plan")
        assert client.post("/findings/1/plan/reject").json()["status"] == "rejected"
        # a rejected plan is not approved -> retest still refused (AC1)
        assert client.post("/findings/1/retest").status_code == 409
        # nothing left to reject now
        assert client.post("/findings/1/plan/reject").status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_approval_api.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_plan_agent'`.

- [ ] **Step 3: Implement the endpoints in `src/revalid/app.py`**

Reconcile the import block. `from revalid.allowlist import load_allowlist` is **already present** (leave it). Add the stdlib `datetime` import at the top with the others, add `from pydantic_ai import Agent`, add the new `revalid.approval` / `revalid.plan` imports, and **replace** the existing `revalid.db`, `revalid.domain`, and `revalid.retest` lines with the widened ones below. After `ruff check --fix`, the block should be:

```python
from datetime import datetime
...  # existing stdlib/third-party imports (httpx, fastapi, pydantic, sqlalchemy)
from pydantic_ai import Agent
...
from revalid.allowlist import load_allowlist  # already present — do not duplicate
from revalid.approval import (
    AllActionsRejectedError,
    NoProposedPlanError,
    PlanNotApprovedError,
    approve_plan,
    edit_plan,
    execute_approved_plan,
    list_plans,
    reject_plan,
    save_generated_plan,
)
from revalid.db import FindingRecord, PlanRecord, VerdictRecord, create_db_engine, session_factory
from revalid.domain import Finding, Probe, Verdict
from revalid.plan import PlannedAction, RejectedAction, build_plan_agent, generate_plan
from revalid.retest import build_probe_client, lab_base_url
```

(The `revalid.retest` line drops both `login_sqli_probe` and `run_probe`: the rewired endpoint routes through `execute_approved_plan`, which calls `run_probe` internally in `approval.py`. `build_probe_client` backs `get_probe_client`; `lab_base_url` backs the plan endpoints. `revalid.domain` gains `Probe`; `revalid.db` gains `PlanRecord`.)

Add `plan_version` to `VerdictOut`:

```python
class VerdictOut(Verdict):
    """A persisted verdict as returned by the API (domain model + linkage)."""

    id: int
    finding_id: int
    probe_kind: str
    plan_version: int | None = None
```

Add the `PlanOut` model after `VerdictOut`:

```python
class PlanOut(BaseModel):
    """A persisted retest-plan version as returned by the API (FR-05)."""

    id: int
    finding_id: int
    version: int
    status: str
    origin: str
    actions: tuple[Probe, ...]
    rejected_actions: tuple[RejectedAction, ...]
    raw: dict[str, Any]
    decided_at: datetime | None
    decided_by: str | None

    @classmethod
    def from_record(cls, record: PlanRecord) -> "PlanOut":
        """Build the API view from a persisted plan row."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            version=record.version,
            status=record.status,
            origin=record.origin,
            actions=record.probes(),
            rejected_actions=tuple(
                RejectedAction.model_validate(item) for item in record.rejected_actions
            ),
            raw=record.raw,
            decided_at=record.decided_at,
            decided_by=record.decided_by,
        )
```

Add the plan-agent dependency at module scope (next to `get_probe_client`):

```python
def get_plan_agent() -> Agent[None, list[PlannedAction]]:
    """Yield the FR-04 plan agent (overridden in tests with a stand-in model)."""
    return build_plan_agent()
```

Inside `create_app`, add the dependency alias next to `ProbeClientDep`:

```python
    PlanAgentDep = Annotated[Agent[None, list[PlannedAction]], Depends(get_plan_agent)]  # noqa: N806
```

Add the endpoints (place them after `list_findings`, before `retest_finding`):

```python
    @app.post("/findings/{finding_id}/plan", response_model=PlanOut)
    def create_plan(finding_id: int, session: SessionDep, agent: PlanAgentDep) -> PlanOut:
        """Generate a retest plan (FR-04) and persist it as a proposed version (FR-05)."""
        finding = session.get(FindingRecord, finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        result = generate_plan(agent, finding.to_domain(), load_allowlist(), lab_base_url())
        if result.error:
            raise HTTPException(status_code=422, detail=f"plan generation failed: {result.error}")
        return PlanOut.from_record(save_generated_plan(session, finding_id, result))

    @app.put("/findings/{finding_id}/plan", response_model=PlanOut)
    def edit_plan_endpoint(
        finding_id: int, actions: list[PlannedAction], session: SessionDep
    ) -> PlanOut:
        """Replace the plan with edited actions as a new proposed version (FR-05)."""
        finding = session.get(FindingRecord, finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        try:
            record, _ = edit_plan(
                session,
                finding_id,
                actions,
                load_allowlist(),
                lab_base_url(),
                finding_title=finding.title,
            )
        except AllActionsRejectedError as exc:
            raise HTTPException(
                status_code=422, detail="all edited actions were rejected by the allowlist/method gate"
            ) from exc
        return PlanOut.from_record(record)

    @app.post("/findings/{finding_id}/plan/approve", response_model=PlanOut)
    def approve_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Approve the latest proposed plan version (FR-05)."""
        if session.get(FindingRecord, finding_id) is None:
            raise HTTPException(status_code=404, detail="finding not found")
        try:
            return PlanOut.from_record(approve_plan(session, finding_id))
        except NoProposedPlanError as exc:
            raise HTTPException(status_code=409, detail="no proposed plan to approve") from exc

    @app.post("/findings/{finding_id}/plan/reject", response_model=PlanOut)
    def reject_plan_endpoint(finding_id: int, session: SessionDep) -> PlanOut:
        """Reject the latest proposed plan version (FR-05)."""
        if session.get(FindingRecord, finding_id) is None:
            raise HTTPException(status_code=404, detail="finding not found")
        try:
            return PlanOut.from_record(reject_plan(session, finding_id))
        except NoProposedPlanError as exc:
            raise HTTPException(status_code=409, detail="no proposed plan to reject") from exc

    @app.get("/findings/{finding_id}/plans", response_model=list[PlanOut])
    def get_plans(finding_id: int, session: SessionDep) -> list[PlanOut]:
        """List all plan versions for a finding (FR-05)."""
        if session.get(FindingRecord, finding_id) is None:
            raise HTTPException(status_code=404, detail="finding not found")
        return [PlanOut.from_record(record) for record in list_plans(session, finding_id)]
```

Replace the body of `retest_finding` with the plan-driven version and change its response model:

```python
    @app.post("/findings/{finding_id}/retest", response_model=list[VerdictOut])
    def retest_finding(
        finding_id: int, session: SessionDep, client: ProbeClientDep
    ) -> list[VerdictOut]:
        """Execute the finding's APPROVED plan and persist the verdicts (FR-05/FR-07/FR-09)."""
        if session.get(FindingRecord, finding_id) is None:
            raise HTTPException(status_code=404, detail="finding not found")
        try:
            records = execute_approved_plan(session, client, finding_id)
        except PlanNotApprovedError as exc:
            raise HTTPException(
                status_code=409, detail="no approved plan; approve one before retesting"
            ) from exc
        return [
            VerdictOut(
                id=r.id,
                finding_id=r.finding_id,
                probe_kind=r.probe_kind,
                plan_version=r.plan_version,
                **r.to_domain().model_dump(),
            )
            for r in records
        ]
```

Also surface the stamp in `GET /verdicts` — add `plan_version=r.plan_version,` to the `VerdictOut(...)` construction inside the existing `list_verdicts` endpoint (so the persisted executed-version is visible there too).

- [ ] **Step 4: Rewrite `tests/unit/test_retest_api.py` for the new contract**

Replace the file contents with:

```python
"""Unit tests for the rewired retest endpoint: execution requires approval (FR-05).

The probe client is an ``httpx.MockTransport`` and the plan agent is a
``FunctionModel``, so the flow runs off-network. A *generated* action becomes a
``planned-http`` probe, which assesses as ``inconclusive``/``no_assessor`` here;
the login probe's still-open verdict is covered in ``test_retest.py`` and
``test_approval_execute.py``.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_plan_agent, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine
from revalid.plan import PlannedAction, build_plan_agent

FINDING_EXPORT: dict[str, object] = {
    "scan_type": "Manual pentest",
    "findings": [
        {
            "title": "SQL injection auth bypass in login",
            "severity": "Critical",
            "endpoints": ["http://localhost:3000/rest/user/login"],
        }
    ],
}

_SQLI_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with an authentication token means still open.",
}

Handler = Callable[[httpx.Request], httpx.Response]


def _agent() -> Agent[None, list[PlannedAction]]:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args={"response": [_SQLI_ACTION]})])

    return build_plan_agent(FunctionModel(respond))


def _make_client(handler: Handler) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))

    def override() -> Iterator[httpx.Client]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[get_probe_client] = override
    app.dependency_overrides[get_plan_agent] = _agent
    return TestClient(app)


def _token_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"authentication": {"token": "t"}})


def _approve(client: TestClient) -> None:
    client.post("/findings/import", json=FINDING_EXPORT)
    client.post("/findings/1/plan")
    client.post("/findings/1/plan/approve")


def test_retest_requires_approval() -> None:
    with _make_client(_token_response) as client:
        client.post("/findings/import", json=FINDING_EXPORT)
        client.post("/findings/1/plan")
        assert client.post("/findings/1/retest").status_code == 409  # AC1


def test_retest_executes_approved_plan_and_stamps_version() -> None:
    with _make_client(_token_response) as client:
        _approve(client)
        verdicts = client.post("/findings/1/retest").json()
        # Generated planned-http probe -> inconclusive (FR-08/09 add matchers);
        # the chokepoint still ran it against /rest/user/login and stamped v1.
        assert verdicts[0]["status"] == "inconclusive"
        assert verdicts[0]["reason_code"] == "no_assessor"
        assert verdicts[0]["plan_version"] == 1
        assert verdicts[0]["evidence"]["request_url"].endswith("/rest/user/login")

        listed = client.get("/verdicts").json()
        assert listed[0]["plan_version"] == 1


def test_retest_unknown_finding_is_404() -> None:
    with _make_client(_token_response) as client:
        assert client.post("/findings/999/retest").status_code == 404


def test_verdicts_empty_initially() -> None:
    with _make_client(_token_response) as client:
        assert client.get("/verdicts").json() == []
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/unit/test_retest_api.py tests/integration/test_approval_api.py -v
uv run pytest tests/unit tests/integration -q
uv run mypy --strict src tests
uv run ruff check src tests
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/revalid/app.py tests/unit/test_retest_api.py tests/integration/test_approval_api.py
git commit -m "feat(app): FR-05 plan review/approve endpoints; retest requires approval (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: System test (app path) + demo + Makefile

**Files:**
- Modify: `tests/system/test_retest_system.py`, `Makefile`
- Create: `scripts/demo/approval_gate.py`

**Interfaces:**
- Consumes: the endpoints from Task 6; `create_app`; `approve_plan`/`save_generated_plan`; the top-level `login_sqli_probe`/`lab_base_url`/`_wait_for_lab`.
- Produces: `make demo-approval`; a live-lab test of the rewired chokepoint.

- [ ] **Step 1: Add the app-path system test** — append to `tests/system/test_retest_system.py`:

```python
def test_approved_plan_retest_still_open_via_api() -> None:
    """Rewired chokepoint end-to-end: seed+approve a login-SQLi plan, retest via API (FR-05).

    Approval/execution is LLM-free, so the plan is seeded with the real
    ``sqli-login-bypass`` probe (kind that ``assess`` understands) rather than
    generated — a *generated* ``planned-http`` probe would assess as inconclusive.
    The default probe client hits the live lab through the FR-06 transport.
    """
    from fastapi.testclient import TestClient

    from revalid.app import create_app
    from revalid.approval import approve_plan, save_generated_plan
    from revalid.db import IN_MEMORY, create_db_engine, session_factory
    from revalid.domain import RetestPlan
    from revalid.plan import PlanResult

    base_url = lab_base_url()
    if not _wait_for_lab(base_url):
        pytest.skip(f"lab not reachable at {base_url}; run `make lab-up`")

    engine = create_db_engine(IN_MEMORY)
    app = create_app(engine=engine)
    with TestClient(app) as client:
        client.post(
            "/findings/import",
            json={"findings": [{"title": "SQLi login", "severity": "Critical"}]},
        )
        with session_factory(engine)() as session:
            plan = RetestPlan(
                finding_title="SQLi login",
                actions=(login_sqli_probe(base_url),),
                raw={"finding_title": "SQLi login"},
            )
            save_generated_plan(session, 1, PlanResult(plan=plan))
            approve_plan(session, 1)
        verdicts = client.post("/findings/1/retest").json()

    assert verdicts[0]["status"] == "still_open"
    assert verdicts[0]["reason_code"] == "sqli_auth_bypass_succeeded"
    assert verdicts[0]["plan_version"] == 1
```

> This exercises the real login-SQLi probe against the live lab through the approval chokepoint. Keep the existing `test_login_sqli_still_open_against_lab` as-is.

- [ ] **Step 2: Create `scripts/demo/approval_gate.py`**

```python
"""Demo for FR-05: nothing executes without approval.

Usage::

    uv run python scripts/demo/approval_gate.py

Runs fully offline against an in-memory app and a mock probe target: import a
finding, show the retest refused (409) before approval, generate a plan, edit it
(v2), approve, and retest — printing the version-stamped verdict.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_plan_agent, get_probe_client
from revalid.db import IN_MEMORY, create_db_engine
from revalid.plan import PlannedAction, build_plan_agent

_ACTION: dict[str, Any] = {
    "method": "POST",
    "target": "/rest/user/login",
    "headers": {"Content-Type": "application/json"},
    "json_body": {"email": "' OR 1=1--", "password": "x"},
    "expected_indicator": "HTTP 200 with an authentication token means still open.",
}


def _agent() -> Agent[None, list[PlannedAction]]:
    def respond(_m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args={"response": [_ACTION]})])

    return build_plan_agent(FunctionModel(respond))


def _probe_client() -> Iterator[httpx.Client]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def main() -> int:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_plan_agent] = _agent
    app.dependency_overrides[get_probe_client] = _probe_client
    with TestClient(app) as client:
        client.post("/findings/import", json={"findings": [{"title": "SQLi login", "severity": "Critical"}]})
        print("1. retest before approval:", client.post("/findings/1/retest").status_code, "(refused)")
        print("2. generate plan:", client.post("/findings/1/plan").json()["status"], "v1")
        edited = client.put("/findings/1/plan", json=[_ACTION]).json()
        print(f"3. edit plan: v{edited['version']} ({edited['origin']})")
        print("4. approve:", client.post("/findings/1/plan/approve").json()["status"])
        verdict = client.post("/findings/1/retest").json()[0]
        print(f"5. retest: {verdict['status']} (executed plan v{verdict['plan_version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Add the Makefile target** — after the `demo-plan` block:

```makefile
# FR-05 demo: nothing executes without approval — refused -> approve -> retest,
# plus an edit creating v2 (offline: in-memory app + mock probe target)
demo-approval:
	uv run python scripts/demo/approval_gate.py
```

- [ ] **Step 4: Run the demo and confirm output**

Run: `make demo-approval`
Expected: prints steps 1–5, e.g. ending with `5. retest: inconclusive (executed plan v2)` — the gate ran the approved v2 and stamped the version; a generated `planned-http` probe assesses as inconclusive until FR-08/09 add matchers. The point demonstrated is the gate + versioning (step 1 refused with 409, step 3 produced v2).

- [ ] **Step 5: Commit**

```bash
git add tests/system/test_retest_system.py scripts/demo/approval_gate.py Makefile
git commit -m "test(system): app-path approval retest; add demo-approval (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: ADR-0012 + docs sync

**Files:**
- Create: `docs/adr/0012-server-side-plan-approval-gate.md` (use the `adr` skill for numbering/format)
- Modify: `docs/adr/README.md` (index), `docs/roadmap.md`, `docs/requirements/srs.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Write ADR-0012 (status `proposed`)** via the `adr` skill. It must record: the versioned plan-row model; the ≤1-proposed/≤1-approved state machine; the single `execute_approved_plan` chokepoint as the AC1 guarantee; edited actions re-gated through FR-06 (`gate_actions` reuse); minimal-audit-now (decision fields + `VerdictRecord.plan_version`) with FR-10 to unify the trail; and the deferrals (generic assessment → FR-08/09, rich edit UI → FR-11). Link ADR-0011 (FR-04) and ADR-0002 (stack).

- [ ] **Step 2: Tick FR-05 acceptance criteria in `docs/requirements/srs.md`**

Change the FR-05 acceptance-criteria checkboxes (lines under "### FR-05") to:

```markdown
- **Acceptance criteria**:
  - [x] Unapproved plans are not executable through any code path (enforced server-side, not only in UI).
  - [x] Plan edits are versioned; the executed version is recorded in the audit trail.
```

- [ ] **Step 3: Update `docs/roadmap.md`**

In the M3 section, tick the FR-05 line:

```markdown
- [x] Server-side approval gate; plans versioned; nothing unapproved executes (FR-05) — `src/revalid/approval.py` (ADR-0012): versioned `plans` rows, a single `execute_approved_plan` chokepoint that refuses without an `approved` version (AC1), edit + regenerate versioning with edited actions re-gated through FR-06 (AC2/D4), executed version stamped on each verdict. `make demo-approval` shows retest refused → approve → retest; the retest endpoint now returns per-probe verdicts.
```

Update the **Current state** line's "Next actions" to drop FR-05 and lead with the FR-11 SPA (#16); note ADR-0012 is `proposed`. Keep it one paragraph, same style as the existing entry.

- [ ] **Step 4: Run the full suite + doc build once**

Run:
```bash
uv run pytest -q -m "not system"
uv run mypy --strict src tests
uv run ruff check src tests
uv run ruff format --check src tests
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/adr docs/roadmap.md docs/requirements/srs.md
git commit -m "docs(plan): ADR-0012 approval gate; tick FR-05, update roadmap (#10)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (before PR)

- [ ] `make test-unit && make test-integration` green; `uv run pytest -m system` green with `make lab-up` (or documented skip).
- [ ] `uv run mypy --strict src tests`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, and `uv run xenon --max-absolute C src` all clean.
- [ ] Coverage ≥ 80% on `src/revalid/approval.py` and the changed modules.
- [ ] `make demo-approval` runs and shows refused → approve → retest.
- [ ] Open the PR with a filled "How to validate" section (commands above + AC checkboxes), targeting `main`, referencing #10.

## Self-review notes (author)

- **Spec coverage**: D1 → Tasks 4–6 (chokepoint + rewired retest); D2 → Task 3 stamp + Task 8 audit fields; D3 → Task 6 `PUT` + Task 4 versioning; D4 → Task 1 `gate_actions` reused in `edit_plan`; D5 → Task 2 dispatch; D6 → Task 4 state machine + tests. AC1 → Task 5/6 refusal tests; AC2 → Task 5/6 version stamp + Task 6 versioning test.
- **Deviation from spec §7**: the existing direct-`run_probe` system test is **kept** and the app-path test is **added** (strictly more coverage) rather than replacing it.
- **Assessment-kind reality (D5), load-bearing for test expectations**: FR-04 generation and the edit endpoint both emit probes of `kind="planned-http"`, which `run_probe` routes to `assess_generic` → `inconclusive`/`no_assessor`. Only a `sqli-login-bypass`-kind probe yields `still_open`/`fixed`. So: API/integration tests generate via the agent and assert **inconclusive** + `plan_version` (they test the *gate*, not verdict logic); the `still_open` end-to-end is proven only where a `login_sqli_probe` is **seeded** — the unit `test_approval_execute.py` (mock) and the live-lab system test (real). Verdict logic itself stays covered by `test_retest.py`. This is honest to the system's current capability; turning `expected_indicator` into a matcher is FR-08/FR-09.
- **Type consistency**: `gate_actions` returns `(list[Probe], list[RejectedAction])` everywhere; `PlanRecord.from_plan` takes `rejected_actions: list[dict]` (approval dumps before calling); `edit_plan(..., *, finding_title: str)` (endpoint passes it from the loaded finding); `execute_approved_plan -> list[VerdictRecord]`; `VerdictOut.plan_version: int | None`; all test helpers annotated (mypy `strict` covers `tests`).
