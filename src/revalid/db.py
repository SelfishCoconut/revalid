"""SQLite persistence layer via SQLAlchemy 2.0 (ADR-0002).

Single-file zero-ops storage. Findings, retest sessions, verdicts, and the
audit trail (FR-10) all live here; only findings exist in the walking skeleton.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, ForeignKey, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from revalid.domain import (
    Finding,
    FindingOrigin,
    Settings,
    Severity,
    VerdictStatus,
)

IN_MEMORY = ":memory:"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class ReportRecord(Base):
    """An uploaded pentest report and its ingest-job status (FR-01/FR-11).

    One row per uploaded PDF; it doubles as the ingest job the UI polls
    (:class:`~revalid.domain.ReportStatus`). ``model`` records the LLM backend
    used (NFR-02 lineage); ``error`` holds the failure message when
    ``status`` is ``failed``. Its findings link back via
    :attr:`FindingRecord.report_id`.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(default=None)
    finding_count: Mapped[int] = mapped_column(default=0)
    #: Soft-hidden from the overview but kept (reversible); deletable (FR-11, #128).
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FindingRecord(Base):
    """Stable identity of a finding (FR-16, ADR-0024).

    The finding's *content* lives in append-only :class:`FindingVersionRecord`
    rows; this row is the stable handle that :attr:`VerdictRecord.finding_id`
    references, so amending a finding (appending a new version) never orphans its
    verdicts. Notes link back via :attr:`FindingNoteRecord.finding_id`.
    """

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FindingVersionRecord(Base):
    """One immutable version of a finding's content (FR-16, ADR-0024).

    Extraction/import lands version 1 (``origin=extraction``); each operator edit
    appends a new version (``origin=edit``). The *current* version is the highest
    ``version`` — older ones are kept, never mutated (append-only version history,
    FR-16). ``edited_by``/``reason`` capture the edit lineage (FR-10); they stay
    ``None``/empty on the extraction version.
    """

    __tablename__ = "finding_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    version: Mapped[int]
    origin: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(16))
    description: Mapped[str]
    impact: Mapped[str] = mapped_column(default="")
    attack_vector: Mapped[str] = mapped_column(default="")
    affected_endpoints: Mapped[list[str]] = mapped_column(JSON)
    reproduction_steps: Mapped[list[str]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    edited_by: Mapped[str | None] = mapped_column(String(32), default=None)
    reason: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @classmethod
    def from_domain(
        cls,
        finding_id: int,
        finding: Finding,
        *,
        version: int,
        origin: FindingOrigin,
        edited_by: str | None = None,
        reason: str = "",
    ) -> FindingVersionRecord:
        """Build a version row from a domain finding (extraction or an edit)."""
        return cls(
            finding_id=finding_id,
            version=version,
            origin=origin.value,
            title=finding.title,
            severity=finding.severity.value,
            description=finding.description,
            impact=finding.impact,
            attack_vector=finding.attack_vector,
            affected_endpoints=list(finding.affected_endpoints),
            reproduction_steps=list(finding.reproduction_steps),
            raw=finding.raw,
            edited_by=edited_by,
            reason=reason,
        )

    def to_domain(self) -> Finding:
        """Convert this version's content back to a domain finding."""
        return Finding(
            title=self.title,
            severity=Severity(self.severity),
            description=self.description,
            impact=self.impact,
            attack_vector=self.attack_vector,
            affected_endpoints=tuple(self.affected_endpoints),
            reproduction_steps=tuple(self.reproduction_steps),
            raw=self.raw,
        )


class FindingNoteRecord(Base):
    """One append-only, stage-tagged note on a finding (FR-16, ADR-0024).

    Notes are the operator's reasoning trail: free text, tagged with the pipeline
    stage it was written on (:class:`~revalid.domain.FindingStage`) and never
    edited or deleted — history is kept, like every other record here.
    """

    __tablename__ = "finding_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    stage: Mapped[str] = mapped_column(String(16))
    body: Mapped[str]
    author: Mapped[str] = mapped_column(String(32), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class VerdictRecord(Base):
    """A persisted agentic retest verdict, linked to its finding + session (FR-09/FR-17).

    Every verdict is the conclusion of an agentic retest session (the batch
    verdict path retired in FR-17 6b-iii): ``session_id`` links the session whose
    append-only transcript justifies it, and ``evidence`` is the flexible
    :class:`~revalid.domain.AgenticEvidence` proof the agent pinned on conclude
    (6b-i), or ``NULL`` for a human adjudication that ran no command. ``actor`` is
    ``"agent"`` for the auto-persisted conclusion or ``"operator"`` for an
    adjudication that supersedes it (latest id wins, FR-10 append-only).
    """

    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("retest_sessions.id"), default=None)
    status: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str]
    matched_indicators: Mapped[list[str]] = mapped_column(JSON)
    #: The flexible :class:`~revalid.domain.AgenticEvidence` proof, or ``NULL``.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: ``"agent"`` (auto-persisted conclusion) or ``"operator"`` (adjudication).
    actor: Mapped[str] = mapped_column(String(32), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @classmethod
    def agentic(
        cls,
        *,
        finding_id: int,
        session_id: int,
        status: VerdictStatus,
        rationale: str,
        actor: str,
        reason_code: str,
        evidence: dict[str, Any] | None = None,
    ) -> VerdictRecord:
        """Build a verdict row for a retest session's conclusion (FR-17).

        ``evidence`` is the flexible :class:`~revalid.domain.AgenticEvidence` proof
        the agent pinned on conclude (6b-i), or ``None`` when unavailable (e.g. a
        human adjudication). ``actor`` is ``"agent"`` for the auto-persisted
        conclusion or ``"operator"`` for a human adjudication that supersedes it.
        """
        return cls(
            finding_id=finding_id,
            status=status.value,
            reason_code=reason_code,
            rationale=rationale,
            matched_indicators=[],
            evidence=evidence,
            session_id=session_id,
            actor=actor,
        )


class RetestSessionRecord(Base):
    """An FR-17 agentic retest session (parent of its append-only transcript).

    Mirrors :class:`VerdictRecord`'s finding link (``finding_id`` FK) but tracks a
    live, in-progress agent run rather than a concluded outcome: ``status`` moves
    through :class:`~revalid.domain.RetestSessionStatus` until a terminal state,
    at which point ``ended_at`` is set. ``verdict_status``/``verdict_rationale``
    are populated only once the session concludes with a verdict.
    """

    __tablename__ = "retest_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    verdict_status: Mapped[str | None] = mapped_column(String(16), default=None)
    verdict_rationale: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    free_launch: Mapped[bool] = mapped_column(default=False)


class SessionEventRecord(Base):
    """One append-only transcript event for a retest session (FR-17 audit).

    The full record of what an agentic session did: each proposed/approved/
    rejected command, its output, state transitions, and the final verdict are
    all rows here, ordered by ``seq`` (monotonic per session, assigned by
    :func:`revalid.retest_session.append_event`) so the transcript replays
    deterministically regardless of wall-clock timestamp resolution.
    """

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("retest_sessions.id"))
    seq: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SettingsRecord(Base):
    """The single-row persisted model/provider setting (FR-13 / ADR-0021).

    One row (``id == 1``) holds the runtime backend selection. The API key is
    stored here in the gitignored SQLite file (ADR-0008) but is never returned
    by the API (write-only, masked on read).
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str | None] = mapped_column(String(256), default=None)
    api_key: Mapped[str | None] = mapped_column(String(256), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @classmethod
    def from_domain(cls, cfg: Settings) -> SettingsRecord:
        """Build the singleton row from a domain settings object."""
        return cls(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
        )

    def to_domain(self) -> Settings:
        """Convert this row back to a domain settings object."""
        return Settings(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
        )


class ChatSessionRecord(Base):
    """A persisted reports-chat conversation thread (FR-18).

    The read-only reports assistant answers natural-language questions about the
    whole corpus (reports, findings, verdicts). A thread is a lightweight
    container for an append-only sequence of :class:`ChatMessageRecord` turns,
    persisted so a conversation survives a page reload. ``title`` is a short
    human label (the first question, truncated) shown in the thread list;
    ``model`` records the LLM backend that answered (NFR-02 lineage).
    """

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    model: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChatMessageRecord(Base):
    """One append-only turn in a reports-chat thread (FR-18).

    ``role`` is ``"user"`` or ``"assistant"``; ``content`` is the plain text.
    Rows are ordered by ``id`` (monotonic insert order). The assistant re-queries
    the DB via its read-only tools on every turn, so only the prose is stored — no
    tool-call parts — and nothing here is ever mutated or deleted in place.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def create_db_engine(path: str = "revalid.db") -> Engine:
    """Create the SQLite engine and ensure the schema exists.

    Args:
        path: Database file path, or :data:`IN_MEMORY` for an in-memory
            database (shared across threads, for tests).

    Returns:
        A ready-to-use engine with all tables created.
    """
    if path == IN_MEMORY:
        # One shared connection so the in-memory db survives FastAPI's
        # per-request worker threads.
        engine = create_engine(
            f"sqlite:///{IN_MEMORY}",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    return engine


def _ensure_columns(engine: Engine) -> None:
    """Add columns introduced after a database was first created (lightweight migration).

    ``create_all`` only creates missing *tables*, never adds columns to existing
    ones, and this single-file app ships no migration framework (ADR-0002). Each
    entry here is an idempotent ``ADD COLUMN`` applied only when absent, so an
    older ``revalid.db`` gains new columns in place instead of needing a reset.
    """
    additions = {
        "reports": {
            "archived": "ALTER TABLE reports ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"
        },
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(ddl)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a sessionmaker bound to the given engine."""
    return sessionmaker(bind=engine)
