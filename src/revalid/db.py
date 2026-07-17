"""SQLite persistence layer via SQLAlchemy 2.0 (ADR-0002).

Single-file zero-ops storage. Findings, plans, runs, and the audit trail
(FR-10) all live here; only findings exist in the walking skeleton.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, ForeignKey, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from revalid.domain import (
    Evidence,
    Finding,
    FindingOrigin,
    PlanStatus,
    Probe,
    RetestPlan,
    Settings,
    Severity,
    Verdict,
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FindingRecord(Base):
    """Stable identity of a finding (FR-16, ADR-0024).

    The finding's *content* lives in append-only :class:`FindingVersionRecord`
    rows; this row is the stable handle that :attr:`PlanRecord.finding_id` and
    :attr:`VerdictRecord.finding_id` reference, so amending a finding (appending a
    new version) never orphans its plans or verdicts. Notes link back via
    :attr:`FindingNoteRecord.finding_id`.
    """

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FindingVersionRecord(Base):
    """One immutable version of a finding's content (FR-16, ADR-0024).

    Extraction/import lands version 1 (``origin=extraction``); each operator edit
    appends a new version (``origin=edit``). The *current* version is the highest
    ``version`` — older ones are kept, never mutated, exactly like a
    :class:`PlanRecord` (ADR-0012). ``edited_by``/``reason`` capture the edit
    lineage (FR-10); they stay ``None``/empty on the extraction version.
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


class PlanRecord(Base):
    """One immutable version of a retest plan for a finding (FR-05).

    Each edit or regeneration inserts a new row (``version`` bumped); a row is
    only ever mutated to record its own decision or to be marked ``superseded``.
    The approval fields (``status``/``decided_at``/``decided_by``) are the
    minimal audit of the review event (FR-10 later unifies the full trail).
    """

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(16))
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    rejected_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Set only on a FAILED version: why background generation produced no plan
    # (ADR-0022), mirroring ReportRecord.error for the async extraction job.
    error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    decided_by: Mapped[str | None] = mapped_column(String(32), default=None)

    @classmethod
    def from_plan(
        cls,
        finding_id: int,
        plan: RetestPlan,
        *,
        version: int,
        status: PlanStatus,
        origin: str,
        rejected_actions: list[dict[str, Any]],
    ) -> PlanRecord:
        """Build a proposed/decided plan row from a domain plan."""
        return cls(
            finding_id=finding_id,
            version=version,
            status=status.value,
            origin=origin,
            actions=[p.model_dump() for p in plan.actions],
            rejected_actions=rejected_actions,
            raw=plan.raw,
        )

    def probes(self) -> tuple[Probe, ...]:
        """Rehydrate the stored actions as runnable probes."""
        return tuple(Probe(**action) for action in self.actions)


class VerdictRecord(Base):
    """Persisted retest verdict linked to the finding it retested (FR-09).

    The ``finding_id`` foreign key and the non-null ``evidence`` column enforce
    the FR-09 invariant in storage: every verdict row references a finding and
    carries the evidence it was derived from.
    """

    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    probe_kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str]
    matched_indicators: Mapped[list[str]] = mapped_column(JSON)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), default=None)
    plan_version: Mapped[int | None] = mapped_column(default=None)
    # Audit trail (FR-10): when the retest ran and who ran it (the executor).
    actor: Mapped[str] = mapped_column(String(32), default="executor")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @classmethod
    def from_domain(
        cls,
        finding_id: int,
        probe_kind: str,
        verdict: Verdict,
        *,
        plan_id: int | None = None,
        plan_version: int | None = None,
        actor: str = "executor",
    ) -> VerdictRecord:
        """Build a row from a domain verdict against ``finding_id`` (FR-10 actor)."""
        return cls(
            finding_id=finding_id,
            probe_kind=probe_kind,
            status=verdict.status.value,
            reason_code=verdict.reason_code,
            rationale=verdict.rationale,
            matched_indicators=list(verdict.matched_indicators),
            evidence=verdict.evidence.model_dump(),
            plan_id=plan_id,
            plan_version=plan_version,
            actor=actor,
        )

    def to_domain(self) -> Verdict:
        """Convert this row back to a domain verdict."""
        return Verdict(
            status=VerdictStatus(self.status),
            reason_code=self.reason_code,
            rationale=self.rationale,
            matched_indicators=tuple(self.matched_indicators),
            evidence=Evidence(**self.evidence),
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
    max_steps: Mapped[int] = mapped_column(default=8)
    max_seconds: Mapped[int | None] = mapped_column(default=None)


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
        return cls(model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key)

    def to_domain(self) -> Settings:
        """Convert this row back to a domain settings object."""
        return Settings(model=self.model, base_url=self.base_url, api_key=self.api_key)


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
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a sessionmaker bound to the given engine."""
    return sessionmaker(bind=engine)
