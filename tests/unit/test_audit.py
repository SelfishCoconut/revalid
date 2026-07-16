"""Unit tests for FR-10 audit-trail re-derivation (ADR-0015).

Acceptance criterion: a re-derivation routine reproduces every stored verdict
from stored data alone — its evidence — with no probe re-execution.
"""

from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from revalid.approval import approve_plan, execute_approved_plan, save_generated_plan
from revalid.audit import rederive_run, rederive_verdict
from revalid.db import IN_MEMORY, VerdictRecord, create_db_engine, session_factory
from revalid.domain import Evidence, Finding, Probe, RetestPlan, Severity, Verdict, VerdictStatus
from revalid.findings import create_finding
from revalid.plan import PlanResult

Handler = Callable[[httpx.Request], httpx.Response]


def _session() -> Session:
    session = session_factory(create_db_engine(IN_MEMORY))()
    create_finding(session, Finding(title="F", severity=Severity.HIGH))
    session.commit()
    return session


def _sqli_generated() -> PlanResult:
    probe = Probe(
        kind="sqli-login-bypass",
        method="POST",
        url="http://localhost:3000/rest/user/login",
        json_body={"email": "' OR 1=1--", "password": "x"},
    )
    plan = RetestPlan(finding_title="F", actions=(probe,), raw={"finding_title": "F"})
    return PlanResult(plan=plan)


def _run_and_store(session: Session, handler: Handler) -> None:
    save_generated_plan(session, 1, _sqli_generated())
    approve_plan(session, 1)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    execute_approved_plan(session, client, 1)


def _evidence(status: int) -> Evidence:
    return Evidence(
        request_method="POST",
        request_url="http://localhost:3000/rest/user/login",
        response_status=status,
    )


def test_rederive_reproduces_a_still_open_verdict() -> None:
    with _session() as session:
        _run_and_store(
            session, lambda _r: httpx.Response(200, json={"authentication": {"token": "t"}})
        )
        report = rederive_run(session)
        assert report.total == 1
        assert report.reproduced == 1
        assert report.ok


def test_rederive_reproduces_an_endpoint_moved_verdict() -> None:
    with _session() as session:
        _run_and_store(session, lambda _r: httpx.Response(404))
        report = rederive_run(session)
        assert report.ok
        assert report.reproduced == report.total == 1


def test_rederive_reproduces_an_unreachable_verdict() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with _session() as session:
        _run_and_store(session, unreachable)
        stored = session.scalars(session.query(VerdictRecord).statement).one()
        assert stored.reason_code == "target_unreachable"  # status-0 evidence path
        assert rederive_run(session).ok


def test_rederive_run_is_empty_when_no_verdicts() -> None:
    with _session() as session:
        report = rederive_run(session)
        assert report.total == 0
        assert report.reproduced == 0
        assert report.ok


def test_rederive_flags_a_verdict_that_no_longer_matches() -> None:
    with _session() as session:
        # A verdict claiming 'fixed' on a 404: the current logic (FR-08 review)
        # re-derives it as inconclusive/endpoint_changed -> a discrepancy.
        bogus = Verdict(
            status=VerdictStatus.FIXED, reason_code="login_rejected", evidence=_evidence(404)
        )
        session.add(VerdictRecord.from_domain(1, "sqli-login-bypass", bogus))
        session.commit()
        report = rederive_run(session)
        assert not report.ok
        assert report.total == 1
        assert report.reproduced == 0
        [discrepancy] = report.discrepancies
        assert discrepancy.stored.startswith("fixed")
        assert "inconclusive" in discrepancy.rederived


def test_rederive_verdict_is_deterministic() -> None:
    evidence = _evidence(200)
    assert rederive_verdict("sqli-login-bypass", evidence) == rederive_verdict(
        "sqli-login-bypass", evidence
    )
