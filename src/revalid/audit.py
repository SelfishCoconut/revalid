"""Audit-trail verdict re-derivation (FR-10, NFR-02, ADR-0015, ADR-0025).

Two verdict shapes re-derive two ways, but the principle is one: a persisted
verdict must be reproducible *from the audit trail alone*, with no probe
re-execution.

- A **batch** verdict (FR-09) is a pure function of its stored evidence: the
  assessment (:func:`revalid.retest.assess_evidence`) and the FR-08 sanity review
  (:func:`revalid.sanity.review_verdict`) are deterministic and take no input
  beyond the evidence, so it re-derives from that evidence.
- An **agentic** verdict (FR-17) is a human-adjudicated judgment over a whole
  session, so its audit trail is that session's append-only transcript
  (ADR-0025's NFR-02 shift). It re-derives by re-projecting the authoritative
  transcript event — the ``verdict`` event for the agent's record, the latest
  ``verdict_adjudicated`` event for an operator adjudication — and confirming the
  stored row still equals it (a denormalization-integrity check).

:func:`rederive_run` recomputes every stored verdict and diffs it against
storage. A clean run proves reproducibility (FR-10 acceptance / NFR-02); any
discrepancy flags a verdict that has drifted from the trail it was derived from.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.db import SessionEventRecord, VerdictRecord
from revalid.domain import Evidence, SessionEventKind, Verdict
from revalid.retest import assess_evidence
from revalid.sanity import review_verdict


def rederive_verdict(probe_kind: str, evidence: Evidence) -> Verdict:
    """Re-derive a verdict from stored evidence alone — no network (FR-10).

    Mirrors the non-network half of :func:`revalid.sanity.guarded_run`: the
    deterministic assessment followed by the FR-08 sanity review.
    """
    return review_verdict(assess_evidence(probe_kind, evidence))


@dataclass(frozen=True)
class Discrepancy:
    """A stored verdict whose re-derivation no longer matches storage.

    Attributes:
        verdict_id: The stored verdict's row id.
        finding_id: The finding the verdict belongs to.
        stored: The stored ``status/reason_code``.
        rederived: The ``status/reason_code`` recomputed from the evidence.
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
        """True iff every stored verdict re-derived exactly from its evidence."""
        return not self.discrepancies


def _rederive_batch(record: VerdictRecord) -> Discrepancy | None:
    """Re-derive a batch verdict from its evidence and diff against storage (FR-09)."""
    stored = record.to_domain()
    rederived = rederive_verdict(record.probe_kind, stored.evidence)
    if rederived == stored:
        return None
    return Discrepancy(
        verdict_id=record.id,
        finding_id=record.finding_id,
        stored=f"{stored.status.value}/{stored.reason_code}",
        rederived=f"{rederived.status.value}/{rederived.reason_code}",
    )


def _transcript_verdict(session: Session, record: VerdictRecord) -> dict[str, str] | None:
    """Return the authoritative transcript verdict payload for an agentic row (FR-17).

    The agent's record (``actor="agent"``) is projected from the session's
    ``verdict`` event; an operator adjudication (``actor="operator"``) from the
    latest ``verdict_adjudicated`` event. ``None`` if the transcript carries no
    such event (the row has no trail to re-derive from — itself a discrepancy).
    """
    kind = (
        SessionEventKind.VERDICT_ADJUDICATED
        if record.actor == "operator"
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

    Reproduces each verdict from the trail alone (FR-10 AC): batch verdicts from
    their persisted evidence, agentic verdicts from their session transcript — no
    probe is re-executed either way. Returns counts and any discrepancies (empty
    when the trail fully reproduces the verdicts).
    """
    records = list(session.scalars(select(VerdictRecord).order_by(VerdictRecord.id)))
    discrepancies: list[Discrepancy] = []
    for record in records:
        discrepancy = (
            _rederive_agentic(session, record)
            if record.source == "agentic"
            else _rederive_batch(record)
        )
        if discrepancy is not None:
            discrepancies.append(discrepancy)
    return AuditReport(
        total=len(records),
        reproduced=len(records) - len(discrepancies),
        discrepancies=tuple(discrepancies),
    )
