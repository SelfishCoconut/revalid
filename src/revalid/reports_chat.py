"""FR-18 reports chat: a read-only analytics agent over the report corpus.

A Pydantic AI agent with typed, **read-only** DB query tools (corpus counts,
report list, finding search, finding detail) so it answers natural-language
questions about ingested reports, findings, and verdicts with *exact* data
pulled from SQLite — never mutating anything and never launching a retest. It
reuses the FR-13 configured backend like every other agent.

The tools are the source of truth, so the agent re-queries the DB on every turn
and only the prose turns of a conversation are persisted
(:class:`~revalid.db.ChatSessionRecord` / :class:`~revalid.db.ChatMessageRecord`).
The query functions here are plain, session-taking helpers — unit-testable
without an LLM — that the agent's tools wrap thinly.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import KnownModelName, Model
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from revalid.db import (
    ChatMessageRecord,
    ChatSessionRecord,
    FindingRecord,
    FindingVersionRecord,
    ReportRecord,
    VerdictRecord,
)
from revalid.findings import current_version
from revalid.llm import agent_model_name, resolve_model

#: Max findings a single search returns; the true total is reported separately so
#: a "how many relate to X" answer stays exact even when the list is truncated.
_SEARCH_LIMIT = 50

#: Chat-thread title length (derived from the first user question).
_TITLE_LEN = 80

USER = "user"
ASSISTANT = "assistant"


# --- Tool return shapes ----------------------------------------------------


class CorpusOverview(BaseModel):
    """Whole-corpus counts (reports, findings, verdicts) for the assistant."""

    reports_total: int
    reports_by_status: dict[str, int]
    findings_total: int
    findings_by_severity: dict[str, int]
    verdicts_total: int
    verdicts_by_status: dict[str, int]


class ReportBrief(BaseModel):
    """A compact report row for the assistant's report list."""

    id: int
    filename: str
    status: str
    finding_count: int
    archived: bool


class FindingBrief(BaseModel):
    """A compact finding row (current version) for search results."""

    id: int
    report_id: int | None
    title: str
    severity: str
    affected_endpoints: list[str]


class FindingSearch(BaseModel):
    """A finding search result: the exact ``total`` plus a (possibly capped) list."""

    total: int
    shown: int
    findings: list[FindingBrief]


class FindingDetail(BaseModel):
    """Full current content of one finding plus its latest verdict, if any."""

    id: int
    report_id: int | None
    title: str
    severity: str
    description: str
    impact: str
    attack_vector: str
    affected_endpoints: list[str]
    reproduction_steps: list[str]
    latest_verdict: str | None
    latest_verdict_rationale: str | None


# --- Read-only query helpers (no LLM; unit-testable) -----------------------


def _current_versions(session: Session) -> list[FindingVersionRecord]:
    """Return the current (highest) version of every finding in the corpus."""
    out: list[FindingVersionRecord] = []
    for finding_id in session.scalars(select(FindingRecord.id).order_by(FindingRecord.id)):
        version = current_version(session, finding_id)
        if version is not None:
            out.append(version)
    return out


def _latest_verdicts(session: Session) -> list[VerdictRecord]:
    """Return the latest verdict per finding (highest id wins), one row each.

    Mirrors the SPA's one-determination-per-finding ledger: verdicts are
    append-only (an operator adjudication supersedes the agent's row), so only
    the newest per finding counts.
    """
    latest: dict[int, VerdictRecord] = {}
    for record in session.scalars(select(VerdictRecord).order_by(VerdictRecord.id)):
        latest[record.finding_id] = record  # higher id overwrites → latest wins
    return list(latest.values())


def corpus_overview(session: Session) -> CorpusOverview:
    """Summarise the whole corpus: report/finding/verdict counts (FR-18).

    Args:
        session: An active read-only DB session.

    Returns:
        Totals plus per-status / per-severity breakdowns. Verdicts are counted
        latest-per-finding (see :func:`_latest_verdicts`).
    """
    reports = list(session.scalars(select(ReportRecord)))
    versions = _current_versions(session)
    verdicts = _latest_verdicts(session)
    return CorpusOverview(
        reports_total=len(reports),
        reports_by_status=dict(Counter(r.status for r in reports)),
        findings_total=len(versions),
        findings_by_severity=dict(Counter(v.severity for v in versions)),
        verdicts_total=len(verdicts),
        verdicts_by_status=dict(Counter(v.status for v in verdicts)),
    )


def list_reports(session: Session, *, include_archived: bool = False) -> list[ReportBrief]:
    """List reports newest first; active only unless ``include_archived`` (FR-18)."""
    stmt = select(ReportRecord).order_by(ReportRecord.id.desc())
    if not include_archived:
        stmt = stmt.where(ReportRecord.archived == False)  # noqa: E712 — SQL boolean, not `is`
    return [
        ReportBrief(
            id=r.id,
            filename=r.filename,
            status=r.status,
            finding_count=r.finding_count,
            archived=r.archived,
        )
        for r in session.scalars(stmt)
    ]


def _haystack(version: FindingVersionRecord) -> str:
    """Concatenate a finding version's searchable text, lower-cased."""
    parts = [
        version.title,
        version.description,
        version.impact,
        version.attack_vector,
        *version.affected_endpoints,
        *version.reproduction_steps,
    ]
    return "\n".join(parts).lower()


def find_findings(
    session: Session,
    *,
    query: str = "",
    severity: str | None = None,
    report_id: int | None = None,
) -> FindingSearch:
    """Search current findings by keyword / severity / report (FR-18, read-only).

    A finding matches when the (case-insensitive) ``query`` occurs anywhere in its
    title, description, impact, attack vector, endpoints, or reproduction steps —
    so "how many findings relate to SQL injection?" is answerable. ``severity`` and
    ``report_id`` further constrain the set.

    Args:
        session: An active read-only DB session.
        query: Case-insensitive substring to match; empty matches everything.
        severity: Exact severity to filter by (``critical``…``info``), or ``None``.
        report_id: Restrict to one report, or ``None`` for all reports.

    Returns:
        The exact ``total`` of matches plus up to :data:`_SEARCH_LIMIT` rows.
    """
    needle = query.strip().lower()
    want_sev = severity.strip().lower() if severity else None
    matches: list[FindingBrief] = []
    for finding_id in session.scalars(select(FindingRecord.id).order_by(FindingRecord.id)):
        version = current_version(session, finding_id)
        if version is None:
            continue
        identity = session.get(FindingRecord, finding_id)
        if identity is None:  # pragma: no cover - id came from the same table
            continue
        if report_id is not None and identity.report_id != report_id:
            continue
        if want_sev is not None and version.severity != want_sev:
            continue
        if needle and needle not in _haystack(version):
            continue
        matches.append(
            FindingBrief(
                id=finding_id,
                report_id=identity.report_id,
                title=version.title,
                severity=version.severity,
                affected_endpoints=list(version.affected_endpoints),
            )
        )
    return FindingSearch(
        total=len(matches),
        shown=min(len(matches), _SEARCH_LIMIT),
        findings=matches[:_SEARCH_LIMIT],
    )


def get_finding(session: Session, finding_id: int) -> FindingDetail | None:
    """Return one finding's full current content + latest verdict, or ``None`` (FR-18)."""
    version = current_version(session, finding_id)
    if version is None:
        return None
    identity = session.get(FindingRecord, finding_id)
    verdict = session.scalars(
        select(VerdictRecord)
        .where(VerdictRecord.finding_id == finding_id)
        .order_by(VerdictRecord.id.desc())
    ).first()
    return FindingDetail(
        id=finding_id,
        report_id=identity.report_id if identity else None,
        title=version.title,
        severity=version.severity,
        description=version.description,
        impact=version.impact,
        attack_vector=version.attack_vector,
        affected_endpoints=list(version.affected_endpoints),
        reproduction_steps=list(version.reproduction_steps),
        latest_verdict=verdict.status if verdict else None,
        latest_verdict_rationale=verdict.rationale if verdict else None,
    )


# --- The agent -------------------------------------------------------------


@dataclass
class ReportsChatDeps:
    """Runtime dependency injected into the reports agent's tools: a DB session.

    Attributes:
        session: The read-only DB session every tool queries through.
        lock: Serialises the tool bodies that share ``session`` (issue #156).
            Pydantic AI runs **sync** tools in worker threads and runs them
            *concurrently* when one model turn emits several tool calls — the
            normal case for a corpus question. A SQLAlchemy ``Session`` is not
            thread-safe, and neither engine this app builds stops the overlap:
            the in-memory engine shares one connection through ``StaticPool``
            with ``check_same_thread=False``, and the pysqlite dialect disables
            that same guard for file databases. Overlapping use therefore
            corrupts the connection's result/parameter state rather than
            raising, surfacing as ``InterfaceError`` or an ``IndexError`` from
            the result proxy. Concurrency buys nothing here — these are short
            local SQLite reads — so the tools take turns.
    """

    session: Session
    lock: AbstractContextManager[bool] = field(default_factory=threading.Lock)


def _read[T](ctx: RunContext[ReportsChatDeps], query: Callable[[Session], T]) -> T:
    """Run one read-only query against the shared session, serialised (issue #156).

    The single seam through which every agent tool touches the DB, so the
    locking discipline is stated once instead of being re-derived in each tool.

    Args:
        ctx: The tool's run context, carrying the session and its lock.
        query: A read-only query function taking the session.

    Returns:
        Whatever ``query`` returns.
    """
    with ctx.deps.lock:
        return query(ctx.deps.session)


_INSTRUCTIONS = """\
You are the revalid reports assistant. You answer the operator's questions about \
the corpus of ingested pentest reports, their findings, and retest verdicts.

Rules:
- You are READ-ONLY. You never change data and never start a retest — you only \
report on what exists.
- Always ground answers in the tools. For any count ("how many reports?", "how \
many findings relate to X?"), call a tool and quote the exact number it returns; \
never estimate. `get_corpus_overview` gives totals and breakdowns; \
`search_findings` gives the exact `total` for a keyword/severity/report query \
(it returns a capped list but an accurate total); `list_all_reports` lists \
reports; `finding_detail` gives one finding's full text and its latest verdict.
- Be concise and specific. If the data does not contain the answer, say so \
plainly rather than guessing. Severities are: critical, high, medium, low, info. \
Verdict statuses are: still_open, fixed, inconclusive.
"""


def build_reports_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[ReportsChatDeps, str]:
    """Build the FR-18 reports assistant: read-only corpus-query tools + a prose answer.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the configured
            backend is used (``REVALID_LLM_MODEL``/settings — FR-13); tests pass
            ``TestModel``/``FunctionModel``.

    Returns:
        An agent whose output is the plain-text answer to show the operator. Its
        tools query the DB carried in :class:`ReportsChatDeps` and mutate nothing.
    """
    agent: Agent[ReportsChatDeps, str] = Agent(
        model if model is not None else resolve_model(),
        deps_type=ReportsChatDeps,
        output_type=str,
        instructions=_INSTRUCTIONS,
        defer_model_check=True,
    )

    # Every tool body goes through `_read`: the tools may run concurrently in
    # worker threads and share one non-thread-safe Session (issue #156).

    @agent.tool
    def get_corpus_overview(ctx: RunContext[ReportsChatDeps]) -> CorpusOverview:
        """Return whole-corpus counts: reports by status, findings by severity, verdicts."""
        return _read(ctx, corpus_overview)

    @agent.tool
    def list_all_reports(
        ctx: RunContext[ReportsChatDeps], include_archived: bool = False
    ) -> list[ReportBrief]:
        """List reports (id, filename, status, finding count); archived excluded by default."""
        return _read(ctx, lambda s: list_reports(s, include_archived=include_archived))

    @agent.tool
    def search_findings(
        ctx: RunContext[ReportsChatDeps],
        query: str = "",
        severity: str | None = None,
        report_id: int | None = None,
    ) -> FindingSearch:
        """Search current findings by keyword/severity/report; returns an exact total."""
        return _read(
            ctx, lambda s: find_findings(s, query=query, severity=severity, report_id=report_id)
        )

    @agent.tool
    def finding_detail(ctx: RunContext[ReportsChatDeps], finding_id: int) -> FindingDetail | None:
        """Return one finding's full content and its latest verdict, or null if unknown."""
        return _read(ctx, lambda s: get_finding(s, finding_id))

    return agent


# --- Thread persistence + answering (FR-18) --------------------------------


def create_chat(session: Session) -> ChatSessionRecord:
    """Create an empty chat thread and return it (committed)."""
    record = ChatSessionRecord()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_chats(session: Session) -> list[ChatSessionRecord]:
    """Return chat threads, most-recently-updated first."""
    return list(
        session.scalars(
            select(ChatSessionRecord).order_by(
                ChatSessionRecord.updated_at.desc(), ChatSessionRecord.id.desc()
            )
        )
    )


def list_messages(session: Session, chat_id: int) -> list[ChatMessageRecord]:
    """Return a thread's messages in insert order (oldest first)."""
    return list(
        session.scalars(
            select(ChatMessageRecord)
            .where(ChatMessageRecord.chat_id == chat_id)
            .order_by(ChatMessageRecord.id)
        )
    )


def delete_chat(session: Session, chat_id: int) -> None:
    """Delete a thread and all its messages (committed); a no-op if it's gone."""
    record = session.get(ChatSessionRecord, chat_id)
    if record is None:
        return
    session.execute(delete(ChatMessageRecord).where(ChatMessageRecord.chat_id == chat_id))
    session.delete(record)
    session.commit()


def _history(messages: list[ChatMessageRecord]) -> list[ModelMessage]:
    """Rebuild Pydantic AI message history from persisted prose turns.

    Only role/content text is stored (the agent re-queries the DB via tools each
    turn), so a user turn becomes a :class:`ModelRequest` and an assistant turn a
    :class:`ModelResponse` with a single text part.
    """
    history: list[ModelMessage] = []
    for message in messages:
        if message.role == USER:
            history.append(ModelRequest(parts=[UserPromptPart(content=message.content)]))
        else:
            history.append(ModelResponse(parts=[TextPart(content=message.content)]))
    return history


def _title_from(question: str) -> str:
    """Derive a short thread title from the first user question."""
    single = " ".join(question.split())
    return single[:_TITLE_LEN] if single else "New chat"


def answer_question(
    agent: Agent[ReportsChatDeps, str],
    session: Session,
    chat: ChatSessionRecord,
    question: str,
) -> ChatMessageRecord:
    """Record the user turn, answer it with the read-only agent, persist the reply (FR-18).

    The prior turns become the agent's message history; the agent answers by
    calling its read-only DB tools over ``session`` and the reply is appended to
    the thread. The first question also sets the thread title. All in one session,
    committed once the reply is stored.

    Args:
        agent: The FR-18 reports agent (a stand-in model in tests).
        session: The active DB session (shared by persistence and the tools).
        chat: The thread to append to.
        question: The operator's new message.

    Returns:
        The persisted assistant reply row.
    """
    prior = list_messages(session, chat.id)
    session.add(ChatMessageRecord(chat_id=chat.id, role=USER, content=question))
    if not prior:
        chat.title = _title_from(question)
    session.commit()

    result = agent.run_sync(
        question, deps=ReportsChatDeps(session=session), message_history=_history(prior)
    )
    answer = result.output.strip() or "(no answer)"

    chat.model = agent_model_name(agent)
    reply = ChatMessageRecord(chat_id=chat.id, role=ASSISTANT, content=answer)
    session.add(reply)
    session.commit()
    session.refresh(reply)
    return reply


async def stream_answer(
    agent: Agent[ReportsChatDeps, str],
    session: Session,
    chat: ChatSessionRecord,
    question: str,
) -> AsyncIterator[str]:
    """Answer like :func:`answer_question` but yield the reply's text as it's generated.

    Same contract — record the user turn (title on the first), run the read-only
    agent over ``session``, append the completed reply — but the reply's tokens are
    yielded as they stream from the model so a streaming transport can show the
    answer live. Persistence and title-setting happen after the stream drains, so
    the stored thread is identical to the blocking path.

    This is an **async** generator so it runs in the request's own event loop: the
    sync ``run_stream_sync`` binds its worker to the calling thread, which breaks
    when a streaming response iterates the generator across threadpool threads, so
    the async :meth:`~pydantic_ai.Agent.run_stream` is used instead.

    Args:
        agent: The FR-18 reports agent (a stand-in model in tests).
        session: The active DB session (shared by persistence and the tools).
        chat: The thread to append to.
        question: The operator's new message.

    Yields:
        Successive text deltas of the assistant's reply, in order.
    """
    prior = list_messages(session, chat.id)
    session.add(ChatMessageRecord(chat_id=chat.id, role=USER, content=question))
    if not prior:
        chat.title = _title_from(question)
    session.commit()

    async with agent.run_stream(
        question, deps=ReportsChatDeps(session=session), message_history=_history(prior)
    ) as result:
        async for delta in result.stream_text(delta=True):
            if delta:
                yield delta
        answer = (await result.get_output()).strip() or "(no answer)"

    chat.model = agent_model_name(agent)
    session.add(ChatMessageRecord(chat_id=chat.id, role=ASSISTANT, content=answer))
    session.commit()
