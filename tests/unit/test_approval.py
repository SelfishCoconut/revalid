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
from revalid.plan import PlannedAction, PlanResult

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
        current = approved_plan(session, 1)
        assert current is not None
        assert current.id == approved.id


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
    off = PlannedAction(method="GET", target="http://evil.example/", expected_indicator="x")
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        with pytest.raises(AllActionsRejectedError):
            edit_plan(session, 1, [off], _GUARD, _BASE_URL, finding_title="F")
        # nothing persisted: v1 remains the only (still proposed) row
        assert [p.version for p in list_plans(session, 1)] == [1]
