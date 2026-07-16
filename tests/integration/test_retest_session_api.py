"""Integration test for the FR-17 agentic retest-session HTTP flow (no network, no LLM).

Exercises the REST surface Task 6 adds on top of the Task 5 orchestrator: starting
a session schedules the first agent step in a background task; Starlette's
``TestClient`` runs ``BackgroundTasks`` to completion *before* each POST returns, so
the transcript is already persisted by the time the response comes back — no
polling is needed. The retest agent is overridden with a scripted
``FunctionModel`` and the sandbox factory with a canned ``FakeSandbox``, so the
whole flow runs off-network and without a live LLM backend.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel
from tests._retest_helpers import script_run_then_conclude

from revalid.app import create_app, get_retest_agent, get_sandbox_factory
from revalid.db import IN_MEMORY, create_db_engine
from revalid.retest_agent import build_retest_agent
from revalid.sandbox import CommandResult, FakeSandbox

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


def _client() -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_retest_agent] = lambda: build_retest_agent(
        FunctionModel(script_run_then_conclude)
    )
    box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
    app.dependency_overrides[get_sandbox_factory] = lambda: lambda _sid: box
    return TestClient(app)


def test_retest_session_flow_proposes_then_concludes_on_approval() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/retest-session")
        assert started.status_code == 202
        sid = started.json()["id"]

        state = client.get(f"/api/retest-sessions/{sid}").json()
        assert state["status"] == "awaiting_command"
        proposed = next(e for e in state["events"] if e["kind"] == "command_proposed")
        cid = proposed["payload"]["tool_call_id"]

        approve = client.post(f"/api/retest-sessions/{sid}/commands/{cid}/approve")
        assert approve.status_code == 202

        final = client.get(f"/api/retest-sessions/{sid}").json()
        assert final["status"] == "concluded"
        assert final["verdict_status"] == "still_open"
        assert any(e["kind"] == "command_output" for e in final["events"])


def test_retest_session_rejecting_the_command_records_the_reason() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/retest-session")
        sid = started.json()["id"]

        state = client.get(f"/api/retest-sessions/{sid}").json()
        proposed = next(e for e in state["events"] if e["kind"] == "command_proposed")
        cid = proposed["payload"]["tool_call_id"]

        reject = client.post(
            f"/api/retest-sessions/{sid}/commands/{cid}/reject", json={"reason": "too risky"}
        )
        assert reject.status_code == 202

        state_after = client.get(f"/api/retest-sessions/{sid}").json()
        rejected = next(e for e in state_after["events"] if e["kind"] == "command_rejected")
        assert rejected["payload"]["reason"] == "too risky"


def test_retest_session_end_marks_it_ended() -> None:
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/retest-session")
        sid = started.json()["id"]

        ended = client.post(f"/api/retest-sessions/{sid}/end")
        assert ended.status_code == 202

        state = client.get(f"/api/retest-sessions/{sid}").json()
        assert state["status"] == "ended"


def test_retest_session_unknown_id_is_404() -> None:
    with _client() as client:
        assert client.get("/api/retest-sessions/999").status_code == 404


def test_retest_session_unknown_finding_is_404() -> None:
    with _client() as client:
        assert client.post("/api/findings/999/retest-session").status_code == 404


def test_retest_session_ends_in_error_when_sandbox_factory_raises() -> None:
    """CORRECTION (Task 5 review): a raising ``make_sandbox`` must not strand the session.

    ``run_first_step`` must catch the failure and settle the session to ``error``
    instead of leaving it in ``starting`` forever.
    """
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_retest_agent] = lambda: build_retest_agent(
        FunctionModel(script_run_then_conclude)
    )

    def _boom(_session_id: int) -> FakeSandbox:
        raise RuntimeError("docker daemon unreachable")

    app.dependency_overrides[get_sandbox_factory] = lambda: _boom
    with TestClient(app) as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/retest-session")
        assert started.status_code == 202
        sid = started.json()["id"]

        state = client.get(f"/api/retest-sessions/{sid}").json()
        assert state["status"] == "error"
        assert any(e["kind"] == "error" for e in state["events"])


def _echo_client() -> TestClient:
    """A client whose sandbox echoes each command, so multiple execs never exhaust it."""
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_retest_agent] = lambda: build_retest_agent(
        FunctionModel(script_run_then_conclude)
    )
    box = FakeSandbox(
        lambda cmd: CommandResult(stdout=f"out:{cmd}", stderr="", exit_code=0, elapsed_ms=1)
    )
    app.dependency_overrides[get_sandbox_factory] = lambda: lambda _sid: box
    return TestClient(app)


def test_retest_session_human_command_runs_and_is_recorded() -> None:
    """A `!` command POSTed to a live session runs ungated and lands in the transcript."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        resp = client.post(f"/api/retest-sessions/{sid}/human-command", json={"command": "whoami"})
        assert resp.status_code == 202

        state = client.get(f"/api/retest-sessions/{sid}").json()
        human = next(e for e in state["events"] if e["kind"] == "human_command")
        assert human["payload"]["command"] == "whoami"
        assert human["payload"]["stdout"] == "out:whoami"


def test_retest_session_human_command_rejects_empty() -> None:
    """An empty command is a 422 (the request model requires a non-empty command)."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        resp = client.post(f"/api/retest-sessions/{sid}/human-command", json={"command": ""})
        assert resp.status_code == 422
