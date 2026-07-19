"""Audit-trail verdict re-derivation (FR-10, NFR-02, ADR-0025, ADR-0030).

A persisted (agentic) verdict must be reproducible *from the audit trail alone*,
with no re-execution. An agentic verdict (FR-17) is a human-adjudicated judgment
over a whole session, so its audit trail is that session's append-only transcript
(ADR-0025's NFR-02 shift). It re-derives by re-projecting the authoritative
transcript event — the ``verdict`` event for the agent's record, the latest
``verdict_adjudicated`` event for an operator adjudication — and confirming the
stored row still equals it (a denormalization-integrity check).

:func:`rederive_run` recomputes every stored verdict and diffs it against
storage. A clean run proves reproducibility (FR-10 acceptance / NFR-02); any
discrepancy flags a verdict that has drifted from the transcript it was derived
from. (The old FR-04/05/07-09 batch verdict re-derivation was removed with the
batch path in FR-17 6b-iii.)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.db import SessionEventRecord, VerdictRecord
from revalid.domain import SessionEventKind


@dataclass(frozen=True)
class Discrepancy:
    """A stored verdict whose re-derivation no longer matches the transcript.

    Attributes:
        verdict_id: The stored verdict's row id.
        finding_id: The finding the verdict belongs to.
        stored: The stored ``status/rationale``.
        rederived: The ``status/rationale`` re-projected from the transcript.
    """

    verdict_id: int
    finding_id: int
    stored: str
    rederived: str


@dataclass(frozen=True)
class AuditReport:
    """Outcome of re-deriving every stored verdict (FR-10 acceptance).

    Attributes:
        total: Number of stored verdicts examined.
        reproduced: How many re-derived identically to storage.
        discrepancies: The verdicts that did not (empty on a clean run).
    """

    total: int
    reproduced: int
    discrepancies: tuple[Discrepancy, ...] = ()

    @property
    def ok(self) -> bool:
        """True iff every stored verdict re-derived exactly from its transcript."""
        return not self.discrepancies


def _transcript_verdict(session: Session, record: VerdictRecord) -> dict[str, str] | None:
    """Return the authoritative transcript verdict payload for an agentic row (FR-17).

    An operator *adjudication* (``reason_code="operator_adjudication"``) is
    projected from the latest ``verdict_adjudicated`` event; every other agentic
    row — the agent's own conclusion and an operator *manual conclude* of a paused
    session (ADR-0034), both ``actor``-tagged but backed by a ``verdict`` event —
    from the session's ``verdict`` event. ``None`` if the transcript carries no
    such event (the row has no trail to re-derive from — itself a discrepancy).
    """
    kind = (
        SessionEventKind.VERDICT_ADJUDICATED
        if record.reason_code == "operator_adjudication"
        else SessionEventKind.VERDICT
    )
    events = session.scalars(
        select(SessionEventRecord)
        .where(
            SessionEventRecord.session_id == record.session_id,
            SessionEventRecord.kind == kind.value,
        )
        .order_by(SessionEventRecord.seq)
    ).all()
    return dict(events[-1].payload) if events else None


def _rederive_agentic(session: Session, record: VerdictRecord) -> Discrepancy | None:
    """Verify an agentic verdict row still equals its authoritative transcript event."""
    payload = _transcript_verdict(session, record)
    stored = f"{record.status}/{record.rationale}"
    if payload is not None and payload["status"] == record.status:
        if payload["rationale"] == record.rationale:
            return None
    rederived = (
        f"{payload['status']}/{payload['rationale']}"
        if payload is not None
        else "<no transcript verdict>"
    )
    return Discrepancy(
        verdict_id=record.id,
        finding_id=record.finding_id,
        stored=stored,
        rederived=rederived,
    )


def rederive_run(session: Session) -> AuditReport:
    """Re-derive every stored verdict from the audit trail and diff against storage.

    Reproduces each agentic verdict from its session transcript (FR-10 AC) — no
    re-execution. Returns counts and any discrepancies (empty when the transcript
    fully reproduces the verdicts).
    """
    records = list(session.scalars(select(VerdictRecord).order_by(VerdictRecord.id)))
    discrepancies: list[Discrepancy] = []
    for record in records:
        discrepancy = _rederive_agentic(session, record)
        if discrepancy is not None:
            discrepancies.append(discrepancy)
    return AuditReport(
        total=len(records),
        reproduced=len(records) - len(discrepancies),
        discrepancies=tuple(discrepancies),
    )
