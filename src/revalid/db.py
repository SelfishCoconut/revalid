"""SQLite persistence layer via SQLAlchemy 2.0 (ADR-0002).

Single-file zero-ops storage. Findings, plans, runs, and the audit trail
(FR-10) all live here; only findings exist in the walking skeleton.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Engine, ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from revalid.domain import Evidence, Finding, Severity, Verdict, VerdictStatus

IN_MEMORY = ":memory:"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class FindingRecord(Base):
    """Persisted row for a :class:`revalid.domain.Finding`."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(16))
    description: Mapped[str]
    affected_endpoints: Mapped[list[str]] = mapped_column(JSON)
    reproduction_steps: Mapped[list[str]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)

    @classmethod
    def from_domain(cls, finding: Finding) -> FindingRecord:
        """Build a row from a domain finding."""
        return cls(
            title=finding.title,
            severity=finding.severity.value,
            description=finding.description,
            affected_endpoints=list(finding.affected_endpoints),
            reproduction_steps=list(finding.reproduction_steps),
            raw=finding.raw,
        )

    def to_domain(self) -> Finding:
        """Convert this row back to a domain finding."""
        return Finding(
            title=self.title,
            severity=Severity(self.severity),
            description=self.description,
            affected_endpoints=tuple(self.affected_endpoints),
            reproduction_steps=tuple(self.reproduction_steps),
            raw=self.raw,
        )


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

    @classmethod
    def from_domain(cls, finding_id: int, probe_kind: str, verdict: Verdict) -> VerdictRecord:
        """Build a row from a domain verdict against ``finding_id``."""
        return cls(
            finding_id=finding_id,
            probe_kind=probe_kind,
            status=verdict.status.value,
            reason_code=verdict.reason_code,
            rationale=verdict.rationale,
            matched_indicators=list(verdict.matched_indicators),
            evidence=verdict.evidence.model_dump(),
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
