"""Integration: the retest path runs only through the allowlist guard (FR-06 x FR-07).

Uses a mock inner transport behind the real :class:`AllowlistTransport`, so the
guard, the executor, the verdict engine, and the ORM are wired together without
touching the network.
"""

from pathlib import Path

import httpx
import pytest

from revalid.allowlist import AllowlistTransport, TargetGuard, TargetNotAllowedError
from revalid.db import VerdictRecord, create_db_engine, session_factory
from revalid.domain import Finding, Severity, VerdictStatus
from revalid.findings import create_finding
from revalid.retest import execute, login_sqli_probe, run_probe

_ALLOWLIST = frozenset({"http://localhost:3000/*"})


def _guarded_client() -> httpx.Client:
    inner = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"authentication": {"token": "t"}})
    )
    return httpx.Client(transport=AllowlistTransport(inner, TargetGuard(_ALLOWLIST)))


@pytest.mark.integration
def test_probe_to_unlisted_target_is_blocked() -> None:
    # The inner transport would answer 200, but the guard must refuse first.
    with _guarded_client() as client, pytest.raises(TargetNotAllowedError):
        execute(client, login_sqli_probe("http://169.254.169.254"))


@pytest.mark.integration
def test_run_probe_does_not_swallow_allowlist_denial() -> None:
    # run_probe only catches httpx.RequestError; an allowlist denial must
    # propagate (fail-closed), never be downgraded to an inconclusive verdict.
    # Guards the invariant against a future refactor that broadens the except.
    with _guarded_client() as client, pytest.raises(TargetNotAllowedError):
        run_probe(client, login_sqli_probe("http://169.254.169.254"))


@pytest.mark.integration
def test_allowlisted_probe_reaches_target_and_verdicts_still_open() -> None:
    with _guarded_client() as client:
        verdict = run_probe(client, login_sqli_probe("http://localhost:3000"))
    assert verdict.status is VerdictStatus.STILL_OPEN
    assert verdict.reason_code == "sqli_auth_bypass_succeeded"


@pytest.mark.integration
def test_verdict_persists_and_reloads(tmp_path: Path) -> None:
    factory = session_factory(create_db_engine(str(tmp_path / "verdicts.db")))
    with factory() as session:
        create_finding(
            session,
            Finding(title="SQL injection auth bypass in login", severity=Severity.CRITICAL),
        )
        session.commit()

    with _guarded_client() as client:
        verdict = run_probe(client, login_sqli_probe("http://localhost:3000"))
    with factory() as session:
        session.add(VerdictRecord.from_domain(1, "sqli-login-bypass", verdict))
        session.commit()

    with factory() as session:
        stored = session.get(VerdictRecord, 1)
        assert stored is not None
        assert stored.finding_id == 1
        assert stored.to_domain() == verdict
