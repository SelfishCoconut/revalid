"""Integration test for the FR-17 WebSocket transcript stream (Task 7).

The WS endpoint tails the same append-only ``session_events`` transcript the
REST poll endpoint (Task 6) reads, but pushes each new event to the client as
it lands instead of requiring the SPA to poll. Starlette's ``TestClient`` runs
``BackgroundTasks`` to completion *before* each POST returns (see
``test_retest_session_api.py``), so by the time ``POST .../retest-session``
returns, ``command_proposed`` is already persisted — the socket replays it
from ``seq`` 0 on connect. Interleaving a REST approve while the socket is
open exercises the tail-and-poll loop against a live, mid-transcript session.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel
from starlette.websockets import WebSocketDisconnect
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


def test_ws_streams_proposed_output_and_verdict() -> None:
    """The socket replays the transcript and tails it to a terminal verdict."""
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        with client.websocket_connect(f"/api/retest-sessions/{sid}/stream") as ws:
            kinds: list[str] = []
            kinds.append(ws.receive_json()["kind"])
            while "command_proposed" not in kinds:
                kinds.append(ws.receive_json()["kind"])
            client.post(f"/api/retest-sessions/{sid}/commands/0/approve")
            while "verdict" not in kinds:
                kinds.append(ws.receive_json()["kind"])

    assert {"command_proposed", "command_output", "verdict"} <= set(kinds)


def test_ws_closes_with_policy_violation_for_unknown_session() -> None:
    """An unknown session id closes the socket immediately (code 1008)."""
    with _client() as client, pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/retest-sessions/999/stream") as ws:
            ws.receive_json()
    assert exc_info.value.code == 1008
