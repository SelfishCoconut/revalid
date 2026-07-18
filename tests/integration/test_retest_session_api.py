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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests._retest_helpers import (
    script_run_then_conclude,
    script_run_then_conclude_noting_message,
)

from revalid.app import create_app, get_goal_agent, get_retest_agent, get_sandbox_factory
from revalid.db import IN_MEMORY, create_db_engine
from revalid.plan import build_goal_agent
from revalid.retest_agent import build_retest_agent
from revalid.sandbox import CommandResult, FakeSandbox

pytestmark = pytest.mark.integration

# The seeded goal the stand-in goal agent emits for every session in these tests.
_GOAL_STEPS = ["Re-check the login endpoint", "Confirm the token"]


def _override_goal_agent(app: FastAPI) -> None:
    """Override the FR-17 goal agent with a stand-in that emits ``_GOAL_STEPS``."""

    def gen(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"steps": _GOAL_STEPS})]
        )

    app.dependency_overrides[get_goal_agent] = lambda: build_goal_agent(FunctionModel(gen))


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
    _override_goal_agent(app)
    box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
    app.dependency_overrides[get_sandbox_factory] = lambda: lambda _sid: box
    return TestClient(app)


def test_session_start_seeds_a_goal() -> None:
    """Starting a session generates a goal and shows it as the first plan_updated (6b-ii)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        state = client.get(f"/api/retest-sessions/{sid}").json()
        goal = next(e for e in state["events"] if e["kind"] == "plan_updated")
        assert goal["payload"]["steps"] == _GOAL_STEPS


def test_set_goal_endpoint_updates_the_panel_event() -> None:
    """A user goal edit appends a fresh plan_updated event (FR-17 6b-ii)."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        resp = client.post(f"/api/retest-sessions/{sid}/goal", json={"steps": ["Only test /admin"]})
        assert resp.status_code == 202
        state = client.get(f"/api/retest-sessions/{sid}").json()
        updates = [e for e in state["events"] if e["kind"] == "plan_updated"]
        assert updates[-1]["payload"]["steps"] == ["Only test /admin"]


def test_regenerate_goal_endpoint_reseeds() -> None:
    """Regenerating re-runs the goal agent and emits a fresh plan_updated (FR-17 6b-ii)."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        client.post(f"/api/retest-sessions/{sid}/goal", json={"steps": ["stale"]})
        resp = client.post(f"/api/retest-sessions/{sid}/goal/regenerate")
        assert resp.status_code == 202
        state = client.get(f"/api/retest-sessions/{sid}").json()
        updates = [e for e in state["events"] if e["kind"] == "plan_updated"]
        assert updates[-1]["payload"]["steps"] == _GOAL_STEPS


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
    _override_goal_agent(app)

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
    _override_goal_agent(app)
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


def test_retest_session_message_recorded_and_buffered() -> None:
    """A chat message POSTed to a live session lands in the transcript."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        resp = client.post(f"/api/retest-sessions/{sid}/message", json={"text": "focus on login"})
        assert resp.status_code == 202

        state = client.get(f"/api/retest-sessions/{sid}").json()
        msg = next(e for e in state["events"] if e["kind"] == "human_message")
        assert msg["payload"]["text"] == "focus on login"


def test_retest_session_message_rejects_empty() -> None:
    """An empty message is a 422 (the request model requires non-empty text)."""
    with _echo_client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        resp = client.post(f"/api/retest-sessions/{sid}/message", json={"text": ""})
        assert resp.status_code == 422


def test_retest_session_message_delivered_on_next_decision() -> None:
    """A queued message reaches the agent on the next approval (over HTTP)."""
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_retest_agent] = lambda: build_retest_agent(
        FunctionModel(script_run_then_conclude_noting_message)
    )
    _override_goal_agent(app)
    box = FakeSandbox(
        lambda cmd: CommandResult(stdout=f"out:{cmd}", stderr="", exit_code=0, elapsed_ms=1)
    )
    app.dependency_overrides[get_sandbox_factory] = lambda: lambda _sid: box
    with TestClient(app) as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        state = client.get(f"/api/retest-sessions/{sid}").json()
        cid = next(e for e in state["events"] if e["kind"] == "command_proposed")["payload"][
            "tool_call_id"
        ]

        client.post(f"/api/retest-sessions/{sid}/message", json={"text": "focus on login"})
        client.post(f"/api/retest-sessions/{sid}/commands/{cid}/approve")

        final = client.get(f"/api/retest-sessions/{sid}").json()
        assert final["verdict_rationale"] == "saw-message"


def test_start_session_in_free_launch_auto_runs_to_verdict() -> None:
    """Starting with free_launch drives the command to a verdict, no approval (FR-17 Slice 5)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post(
            "/api/findings/1/retest-session", json={"free_launch": True, "max_steps": 5}
        )
        assert started.status_code == 202
        sid = started.json()["id"]
        # TestClient runs background tasks to completion before POST returns, so
        # the free-launch loop has already driven to a verdict.
        got = client.get(f"/api/retest-sessions/{sid}").json()
        assert got["free_launch"] is True
        assert got["max_steps"] == 5
        assert got["status"] == "concluded"
        approvals = [e for e in got["events"] if e["kind"] == "command_approved"]
        assert approvals and all(e["payload"].get("auto") is True for e in approvals)


def test_free_launch_toggle_endpoint_drives_pending_command() -> None:
    """The live toggle auto-approves a pending command and records the change (FR-17 Slice 5)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        assert client.get(f"/api/retest-sessions/{sid}").json()["status"] == "awaiting_command"

        resp = client.post(f"/api/retest-sessions/{sid}/free-launch", json={"enabled": True})
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}

        got = client.get(f"/api/retest-sessions/{sid}").json()
        assert got["free_launch"] is True
        assert got["status"] == "concluded"  # the toggle drove the pending command
        assert any(e["kind"] == "free_launch_changed" for e in got["events"])


def test_agentic_verdict_is_queryable_and_adjudicable() -> None:
    """Concluding wires the verdict into /verdicts + /export; adjudication supersedes it (6a)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/retest-session", json={"free_launch": True})
        sid = started.json()["id"]

        concluded = client.get(f"/api/retest-sessions/{sid}").json()
        assert concluded["status"] == "concluded"
        assert concluded["verdict_status"] == "still_open"

        # FR-09 wiring: the agent's verdict is now queryable (agentic, evidence-free).
        verdicts = client.get("/api/verdicts").json()
        assert len(verdicts) == 1
        assert verdicts[0]["source"] == "agentic"
        assert verdicts[0]["actor"] == "agent"
        assert verdicts[0]["status"] == "still_open"
        # Slice 6b-i: the agent pins the decisive command's real output as proof.
        assert verdicts[0]["evidence"] is not None
        assert verdicts[0]["evidence"]["explanation"]
        # FR-10: the agentic verdict re-derives from its transcript.
        assert client.get("/api/audit").json()["ok"] is True

        # Human overrides: fixed.
        adj = client.post(
            f"/api/retest-sessions/{sid}/adjudicate",
            json={"status": "fixed", "rationale": "confirmed patched"},
        )
        assert adj.status_code == 200
        assert adj.json() == {"status": "adjudicated"}

        after = client.get(f"/api/retest-sessions/{sid}").json()
        assert after["verdict_status"] == "fixed"  # the session view shows the final call
        assert any(e["kind"] == "verdict_adjudicated" for e in after["events"])

        # FR-12: the export's latest verdict for the finding is the operator's override.
        export = client.get("/api/export").json()
        finding_verdicts = [v for v in export["verdicts"] if v["finding_id"] == 1]
        assert len(finding_verdicts) == 2  # agent + operator, both retained (append-only)
        latest = max(finding_verdicts, key=lambda v: v["id"])
        assert latest["actor"] == "operator"
        assert latest["status"] == "fixed"
        assert latest["source"] == "agentic"
        # FR-10 still clean after adjudication (operator row checked vs its event).
        assert client.get("/api/audit").json()["ok"] is True

        # The operator's adjudication ran no command, so its verdict has no evidence
        # (Slice 6b-i): /verdicts surfaces that null cleanly.
        listed = client.get("/api/verdicts").json()
        operator = next(v for v in listed if v["actor"] == "operator")
        assert operator["evidence"] is None


def test_adjudicate_rejects_an_invalid_status() -> None:
    """A body with a non-VerdictStatus value is a 422 (FR-17 Slice 6a)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        started = client.post("/api/findings/1/retest-session", json={"free_launch": True})
        sid = started.json()["id"]
        resp = client.post(
            f"/api/retest-sessions/{sid}/adjudicate",
            json={"status": "definitely-not-a-status", "rationale": "x"},
        )
        assert resp.status_code == 422


def test_adjudicate_without_a_verdict_is_a_noop() -> None:
    """Adjudicating a session that has no agent verdict yet writes nothing (FR-17 Slice 6a)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]  # gated: awaiting_command
        resp = client.post(
            f"/api/retest-sessions/{sid}/adjudicate",
            json={"status": "fixed", "rationale": "premature"},
        )
        assert resp.status_code == 200
        assert client.get("/api/verdicts").json() == []  # nothing to supersede → no row written


def test_get_session_returns_budget_defaults() -> None:
    """A default-started session reports gated mode + the default step budget (FR-17 Slice 5)."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]
        got = client.get(f"/api/retest-sessions/{sid}").json()
        assert got["free_launch"] is False
        assert got["max_steps"] == 8
        assert got["max_seconds"] is None
