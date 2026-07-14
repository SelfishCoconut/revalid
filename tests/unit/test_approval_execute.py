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


def test_execute_yields_inconclusive_on_endpoint_moved_404() -> None:
    """FR-08 AC2 through the chokepoint: a 404 stays inconclusive, never fixed."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _session() as session:
        save_generated_plan(session, 1, _sqli_generated())
        approve_plan(session, 1)
        [verdict] = execute_approved_plan(session, _probe_client(handler), 1)
        assert verdict.status == VerdictStatus.INCONCLUSIVE.value
        assert verdict.reason_code == "endpoint_changed"


def test_execute_stamps_the_actual_approved_version() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with _session() as session:
        save_generated_plan(session, 1, _sqli_generated())  # v1, immediately superseded
        save_generated_plan(session, 1, _sqli_generated())  # v2, proposed
        approve_plan(session, 1)  # approves v2
        [verdict] = execute_approved_plan(session, _probe_client(handler), 1)
        assert verdict.status == VerdictStatus.STILL_OPEN.value
        assert verdict.plan_version == 2  # AC2: stamp tracks the real version, not a constant
