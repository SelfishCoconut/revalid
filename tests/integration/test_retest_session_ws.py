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
from starlette.websockets import WebSocketDisconnect
from tests._retest_helpers import script_run_then_conclude, streaming

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
        streaming(script_run_then_conclude)
    )
    box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
    app.dependency_overrides[get_sandbox_factory] = lambda: lambda _sid: box
    return TestClient(app)


def _is_handback(event: dict[str, Any]) -> bool:
    """Is ``event`` the agent parking the session back with the operator?

    Since ADR-0042 the hand-back has no dedicated event kind: it is a
    ``state_change`` to ``awaiting_operator`` (which absorbed ``needs_guidance``).
    """
    return event["kind"] == "state_change" and event["payload"].get("to") == "awaiting_operator"


def test_ws_streams_proposed_output_and_verdict() -> None:
    """The socket replays the transcript and tails it to a terminal verdict.

    Approves using the real ``tool_call_id`` off the ``command_proposed`` event
    (not a placeholder cid): since the final-review Fix 1, ``apply_decision``
    validates the URL ``cid`` against the session's pending call and no-ops on
    a mismatch, so a wrong id here would silently never approve and the
    hand-back wait below would hang.
    """
    with _client() as client:
        client.post("/api/findings/import", json=_IMPORT)
        sid = client.post("/api/findings/1/retest-session").json()["id"]

        with client.websocket_connect(f"/api/retest-sessions/{sid}/stream") as ws:
            events: list[dict[str, Any]] = [ws.receive_json()]
            while not any(e["kind"] == "command_proposed" for e in events):
                events.append(ws.receive_json())
            proposed = next(e for e in events if e["kind"] == "command_proposed")
            cid = proposed["payload"]["tool_call_id"]
            client.post(f"/api/retest-sessions/{sid}/commands/{cid}/approve")
            # Guided mode (ADR-0040) is the default: the approved command runs and
            # the agent hands back a recommendation, not a verdict. Since ADR-0042
            # that hand-back is an ordinary ``agent_message`` plus a state change to
            # ``awaiting_operator`` (the folded-in ``needs_guidance``) — there is no
            # longer a dedicated event kind for it. The operator then records the
            # terminal verdict, which the socket tails.
            while not any(_is_handback(e) for e in events):
                events.append(ws.receive_json())
            client.post(
                f"/api/retest-sessions/{sid}/conclude",
                json={"status": "still_open", "rationale": "confirmed by hand"},
            )
            while not any(e["kind"] == "verdict" for e in events):
                events.append(ws.receive_json())

    kinds = {e["kind"] for e in events}
    assert {"command_proposed", "command_output", "agent_message", "verdict"} <= kinds
    assert any(_is_handback(e) for e in events)


def test_ws_closes_with_policy_violation_for_unknown_session() -> None:
    """An unknown session id closes the socket immediately (code 1008)."""
    with _client() as client, pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/retest-sessions/999/stream") as ws:
            ws.receive_json()
    assert exc_info.value.code == 1008
