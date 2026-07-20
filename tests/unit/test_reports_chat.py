"""Unit tests for the FR-18 reports chat: read-only query tools + thread persistence.

No network and no real model: the query helpers run against an in-memory DB, and
the agent is driven by Pydantic AI's ``FunctionModel``/``TestModel`` so the
persistence + message-history wiring is proven offline.
"""

from collections.abc import Iterator

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy.orm import Session

from revalid.db import (
    IN_MEMORY,
    ChatMessageRecord,
    ReportRecord,
    RetestSessionRecord,
    VerdictRecord,
    create_db_engine,
    session_factory,
)
from revalid.domain import Finding, ReportStatus, RetestSessionStatus, Severity, VerdictStatus
from revalid.findings import create_finding
from revalid.reports_chat import (
    ReportsChatDeps,
    _history,
    _title_from,
    answer_question,
    build_reports_agent,
    corpus_overview,
    create_chat,
    delete_chat,
    find_findings,
    get_finding,
    list_chats,
    list_messages,
    list_reports,
)


@pytest.fixture
def session() -> Iterator[Session]:
    """A fresh in-memory DB session."""
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as db:
        yield db


def _add_report(
    session: Session, filename: str, status: ReportStatus, *, archived: bool = False
) -> int:
    report = ReportRecord(
        filename=filename,
        status=status.value,
        model="stub",
        finding_count=0,
        archived=archived,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report.id


def _add_finding(session: Session, report_id: int, finding: Finding) -> int:
    record = create_finding(session, finding, report_id=report_id)
    session.commit()
    return record.id


def _add_verdict(session: Session, finding_id: int, status: VerdictStatus) -> int:
    sess = RetestSessionRecord(
        finding_id=finding_id, status=RetestSessionStatus.CONCLUDED.value, model="stub"
    )
    session.add(sess)
    session.commit()
    session.refresh(sess)
    verdict = VerdictRecord.agentic(
        finding_id=finding_id,
        session_id=sess.id,
        status=status,
        rationale="because",
        actor="agent",
        reason_code="agentic",
    )
    session.add(verdict)
    session.commit()
    session.refresh(verdict)
    return verdict.id


def _seed(session: Session) -> None:
    """Two reports (one archived), three findings, one verdict."""
    r1 = _add_report(session, "acme.pdf", ReportStatus.READY)
    r2 = _add_report(session, "old.pdf", ReportStatus.FAILED, archived=True)
    f1 = _add_finding(
        session,
        r1,
        Finding(
            title="SQL Injection in login",
            severity=Severity.CRITICAL,
            description="Union-based SQLi via the email field.",
            affected_endpoints=("/rest/user/login",),
        ),
    )
    _add_finding(
        session,
        r1,
        Finding(title="Reflected XSS", severity=Severity.MEDIUM, description="alert(1) in search."),
    )
    _add_finding(
        session,
        r2,
        Finding(title="Verbose error messages", severity=Severity.LOW, description="stack traces"),
    )
    _add_verdict(session, f1, VerdictStatus.STILL_OPEN)


# --- Query helpers ---------------------------------------------------------


def test_corpus_overview_counts(session: Session) -> None:
    _seed(session)
    overview = corpus_overview(session)
    assert overview.reports_total == 2
    assert overview.reports_by_status == {"ready": 1, "failed": 1}
    assert overview.findings_total == 3
    assert overview.findings_by_severity == {"critical": 1, "medium": 1, "low": 1}
    assert overview.verdicts_total == 1
    assert overview.verdicts_by_status == {"still_open": 1}


def test_corpus_overview_empty(session: Session) -> None:
    overview = corpus_overview(session)
    assert overview.reports_total == 0
    assert overview.findings_by_severity == {}
    assert overview.verdicts_total == 0


def test_list_reports_excludes_archived_by_default(session: Session) -> None:
    _seed(session)
    active = list_reports(session)
    assert [r.filename for r in active] == ["acme.pdf"]
    allr = list_reports(session, include_archived=True)
    assert {r.filename for r in allr} == {"acme.pdf", "old.pdf"}


def test_find_findings_by_keyword(session: Session) -> None:
    _seed(session)
    sql = find_findings(session, query="sql injection")
    assert sql.total == 1
    assert sql.shown == 1
    assert sql.findings[0].title == "SQL Injection in login"
    # Endpoint text is searchable too.
    endpoint = find_findings(session, query="/rest/user/login")
    assert endpoint.total == 1


def test_find_findings_by_severity_and_report(session: Session) -> None:
    _seed(session)
    assert find_findings(session, severity="critical").total == 1
    assert find_findings(session, severity="CRITICAL").total == 1  # case-insensitive
    assert find_findings(session, severity="info").total == 0
    # report_id filter: report 1 has 2 findings.
    report1 = list_reports(session)[0].id
    assert find_findings(session, report_id=report1).total == 2


def test_find_findings_empty_query_matches_all(session: Session) -> None:
    _seed(session)
    assert find_findings(session).total == 3


def test_get_finding_returns_detail_with_latest_verdict(session: Session) -> None:
    _seed(session)
    fid = find_findings(session, query="sql").findings[0].id
    detail = get_finding(session, fid)
    assert detail is not None
    assert detail.title == "SQL Injection in login"
    assert detail.affected_endpoints == ["/rest/user/login"]
    assert detail.latest_verdict == "still_open"
    assert detail.latest_verdict_rationale == "because"


def test_get_finding_unknown_is_none(session: Session) -> None:
    assert get_finding(session, 999) is None


def test_get_finding_without_verdict(session: Session) -> None:
    _seed(session)
    xss = find_findings(session, query="xss").findings[0].id
    detail = get_finding(session, xss)
    assert detail is not None
    assert detail.latest_verdict is None
    assert detail.latest_verdict_rationale is None


def test_latest_verdict_supersedes_in_overview(session: Session) -> None:
    r = _add_report(session, "r.pdf", ReportStatus.READY)
    f = _add_finding(session, r, Finding(title="X", severity=Severity.HIGH))
    _add_verdict(session, f, VerdictStatus.STILL_OPEN)
    _add_verdict(session, f, VerdictStatus.FIXED)  # newer → wins
    overview = corpus_overview(session)
    assert overview.verdicts_total == 1
    assert overview.verdicts_by_status == {"fixed": 1}


# --- Pure helpers ----------------------------------------------------------


def test_title_from_truncates_and_collapses_whitespace() -> None:
    assert _title_from("  how   many\nreports? ") == "how many reports?"
    assert _title_from("") == "New chat"
    assert len(_title_from("x" * 200)) == 80


def test_history_maps_roles_to_message_types(session: Session) -> None:
    chat = create_chat(session)
    session.add(ChatMessageRecord(chat_id=chat.id, role="user", content="hi"))
    session.add(ChatMessageRecord(chat_id=chat.id, role="assistant", content="hello"))
    session.commit()
    history: list[ModelMessage] = _history(list_messages(session, chat.id))
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[0].parts[0], UserPromptPart)
    assert isinstance(history[1], ModelResponse)
    assert isinstance(history[1].parts[0], TextPart)


# --- Thread CRUD -----------------------------------------------------------


def test_create_list_delete_chat(session: Session) -> None:
    a = create_chat(session)
    b = create_chat(session)
    threads = list_chats(session)
    assert {c.id for c in threads} == {a.id, b.id}
    delete_chat(session, a.id)
    assert [c.id for c in list_chats(session)] == [b.id]
    # Deleting a gone thread is a no-op.
    delete_chat(session, a.id)


def test_delete_chat_removes_messages(session: Session) -> None:
    chat = create_chat(session)
    session.add(ChatMessageRecord(chat_id=chat.id, role="user", content="hi"))
    session.commit()
    delete_chat(session, chat.id)
    assert list_messages(session, chat.id) == []


# --- The agent + answering -------------------------------------------------


def _fixed_reply(text: str) -> FunctionModel:
    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=text)])

    return FunctionModel(respond)


def test_answer_question_persists_both_turns_and_titles(session: Session) -> None:
    _seed(session)
    agent = build_reports_agent(_fixed_reply("You have 2 reports."))
    chat = create_chat(session)
    reply = answer_question(agent, session, chat, "how many reports do we have?")
    assert reply.role == "assistant"
    assert reply.content == "You have 2 reports."
    turns = list_messages(session, chat.id)
    assert [(m.role, m.content) for m in turns] == [
        ("user", "how many reports do we have?"),
        ("assistant", "You have 2 reports."),
    ]
    session.refresh(chat)
    assert chat.title == "how many reports do we have?"
    assert chat.model.startswith("function")  # the stand-in model's lineage was recorded


def test_answer_question_blank_reply_falls_back(session: Session) -> None:
    agent = build_reports_agent(_fixed_reply("   "))
    chat = create_chat(session)
    reply = answer_question(agent, session, chat, "hi")
    assert reply.content == "(no answer)"


def test_answer_question_multi_turn_passes_history(session: Session) -> None:
    seen: list[int] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        # Count prior user turns visible to the model (proves message_history flows).
        users = sum(
            isinstance(p, UserPromptPart) for m in messages for p in getattr(m, "parts", ())
        )
        seen.append(users)
        return ModelResponse(parts=[TextPart(content="ok")])

    agent: Agent[ReportsChatDeps, str] = build_reports_agent(FunctionModel(respond))
    chat = create_chat(session)
    answer_question(agent, session, chat, "first")
    answer_question(agent, session, chat, "second")
    # First turn sees 1 user prompt; the second sees the prior + the new one.
    assert seen == [1, 2]


def test_agent_tools_execute_read_only(session: Session) -> None:
    """TestModel calls every registered tool once — exercises all four tool wrappers."""
    _seed(session)
    agent = build_reports_agent(TestModel())
    chat = create_chat(session)
    reply = answer_question(agent, session, chat, "summarise everything")
    assert reply.content  # TestModel echoes the tool outputs as JSON
    # Read-only: the corpus is unchanged after the agent ran its tools.
    assert corpus_overview(session).findings_total == 3
