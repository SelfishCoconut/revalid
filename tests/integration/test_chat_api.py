"""Integration test: the FR-18 reports chat is operable end-to-end over /api.

Wires the real app with a deterministic reports agent (a ``FunctionModel`` that
answers from the corpus-overview tool), proving create → ask → persist →
re-read → delete through the ``/api`` surface with no network and no real model.
"""

from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from revalid.app import create_app, get_reports_agent
from revalid.db import IN_MEMORY, ReportRecord, create_db_engine
from revalid.domain import Finding, ReportStatus, Severity
from revalid.findings import create_finding
from revalid.reports_chat import build_reports_agent

pytestmark = pytest.mark.integration


def _overview_then_answer() -> FunctionModel:
    """A model that calls `get_corpus_overview` once, then answers in prose.

    Two-response script: first turn emits the tool call, second turn (after the
    tool result comes back) returns text — exercises the real tool wiring.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        called = any(isinstance(p, ToolCallPart) for m in messages for p in getattr(m, "parts", ()))
        if not called:
            tool = next(t for t in info.function_tools if t.name == "get_corpus_overview")
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args={})])
        return ModelResponse(parts=[TextPart(content="There is 1 report with 1 finding.")])

    return FunctionModel(respond)


@pytest.fixture
def client() -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_reports_agent] = lambda: build_reports_agent(
        _overview_then_answer()
    )
    # Seed one report + finding so the tool has something to report.
    with app.state.sessions() as session:
        report = ReportRecord(
            filename="acme.pdf", status=ReportStatus.READY.value, model="stub", finding_count=1
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        create_finding(
            session,
            Finding(title="SQLi", severity=Severity.CRITICAL, description="x"),
            report_id=report.id,
        )
        session.commit()
    return TestClient(app)


def test_chat_lifecycle_over_api(client: TestClient) -> None:
    # 1. create a thread.
    created = client.post("/api/chats")
    assert created.status_code == 201
    chat_id = created.json()["id"]
    assert created.json()["title"] == "New chat"

    # 2. it appears in the list.
    listing = client.get("/api/chats").json()
    assert [c["id"] for c in listing] == [chat_id]

    # 3. ask a question — the assistant answers from its tool and both turns persist.
    answered = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "how many reports do we have?"}
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["title"] == "how many reports do we have?"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "how many reports do we have?"),
        ("assistant", "There is 1 report with 1 finding."),
    ]

    # 4. the thread is durable: re-reading returns the same transcript.
    reread = client.get(f"/api/chats/{chat_id}").json()
    assert len(reread["messages"]) == 2

    # 5. delete removes it.
    assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_chat_message_streams_sse_and_persists(client: TestClient) -> None:
    # The streaming route needs a stream-capable model; the fixture's FunctionModel
    # cannot stream, so swap in a TestModel. call_tools=[] keeps the streamed run
    # off the DB tools (proven by the blocking test) so this focuses on the SSE
    # transport + persistence without the shared-connection worker-thread flake.
    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_reports_agent] = lambda: build_reports_agent(
        TestModel(call_tools=[], custom_output_text="There is 1 report.")
    )
    chat_id = client.post("/api/chats").json()["id"]

    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/messages/stream",
        json={"content": "how many reports?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    # SSE framing: token frame(s) carrying the reply, then the done sentinel.
    assert "event: token" in body
    assert "There is 1 report." in body
    assert "event: done" in body

    # The completed reply is persisted and readable via the blocking read route.
    reread = client.get(f"/api/chats/{chat_id}").json()
    assert [(m["role"], m["content"]) for m in reread["messages"]] == [
        ("user", "how many reports?"),
        ("assistant", "There is 1 report."),
    ]


def test_unknown_thread_is_404(client: TestClient) -> None:
    assert client.get("/api/chats/999").status_code == 404
    assert client.post("/api/chats/999/messages", json={"content": "hi"}).status_code == 404
    assert client.post("/api/chats/999/messages/stream", json={"content": "hi"}).status_code == 404
    assert client.delete("/api/chats/999").status_code == 404


def test_empty_message_is_422(client: TestClient) -> None:
    chat_id = client.post("/api/chats").json()["id"]
    assert client.post(f"/api/chats/{chat_id}/messages", json={"content": ""}).status_code == 422
