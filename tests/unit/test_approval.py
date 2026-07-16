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
    finish_plan_generation,
    list_plans,
    reject_plan,
    save_generated_plan,
    start_plan_generation,
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


def test_start_reserves_generating_v1() -> None:
    with _session() as session:
        record = start_plan_generation(session, 1)
        assert record.version == 1
        assert record.status == PlanStatus.GENERATING.value
        assert record.origin == "generated"
        assert record.actions == []


def test_finish_settles_generating_to_proposed_in_place() -> None:
    with _session() as session:
        started = start_plan_generation(session, 1)
        settled = finish_plan_generation(session, started.id, _generated())
        assert settled is not None
        # Same row, same version — filled in place so the poll transitions cleanly.
        assert settled.id == started.id
        assert settled.version == 1
        assert settled.status == PlanStatus.PROPOSED.value
        assert [p.url for p in settled.probes()] == ["http://localhost:3000/rest/x"]


def test_finish_with_no_actions_marks_failed_with_reason() -> None:
    empty = PlanResult(plan=RetestPlan(finding_title="F"))
    with _session() as session:
        started = start_plan_generation(session, 1)
        settled = finish_plan_generation(session, started.id, empty)
        assert settled is not None
        assert settled.status == PlanStatus.FAILED.value
        assert settled.error == "no runnable actions could be planned for this finding"


def test_finish_records_generation_error() -> None:
    failed = PlanResult(plan=RetestPlan(finding_title="F"), error="boom")
    with _session() as session:
        started = start_plan_generation(session, 1)
        settled = finish_plan_generation(session, started.id, failed)
        assert settled is not None
        assert settled.status == PlanStatus.FAILED.value
        assert settled.error == "boom"


def test_finish_is_a_noop_once_superseded() -> None:
    with _session() as session:
        stale = start_plan_generation(session, 1)
        # A newer generation supersedes the in-flight one before its result lands.
        fresh = start_plan_generation(session, 1)
        assert stale.status == PlanStatus.SUPERSEDED.value

        # The stale result must not resurrect the superseded row.
        assert finish_plan_generation(session, stale.id, _generated()) is None
        by_version = {p.version: p.status for p in list_plans(session, 1)}
        assert by_version[stale.version] == PlanStatus.SUPERSEDED.value
        # The fresh in-flight version is untouched.
        assert by_version[fresh.version] == PlanStatus.GENERATING.value


def test_start_supersedes_a_live_proposal() -> None:
    with _session() as session:
        save_generated_plan(session, 1, _generated())  # proposed v1
        start_plan_generation(session, 1)  # v2 generating supersedes it
        statuses = {p.version: p.status for p in list_plans(session, 1)}
        assert statuses == {
            1: PlanStatus.SUPERSEDED.value,
            2: PlanStatus.GENERATING.value,
        }


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


def test_edit_as_first_action_creates_proposed_v1() -> None:
    with _session() as session:
        record, rejected = edit_plan(session, 1, [_ACTION], _GUARD, _BASE_URL, finding_title="F")
        assert record.version == 1
        assert record.status == PlanStatus.PROPOSED.value
        assert record.origin == "edited"
        assert rejected == []
        assert [p.version for p in list_plans(session, 1)] == [1]


def test_edit_with_all_actions_off_allowlist_raises() -> None:
    off = PlannedAction(method="GET", target="http://evil.example/", expected_indicator="x")
    with _session() as session:
        save_generated_plan(session, 1, _generated())
        with pytest.raises(AllActionsRejectedError):
            edit_plan(session, 1, [off], _GUARD, _BASE_URL, finding_title="F")
        # nothing persisted: v1 remains the only (still proposed) row
        assert [p.version for p in list_plans(session, 1)] == [1]
