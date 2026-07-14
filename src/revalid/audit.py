"""Audit-trail verdict re-derivation (FR-10, NFR-02, ADR-0015).

A verdict is a pure function of its stored evidence: the assessment
(:func:`revalid.retest.assess_evidence`) and the FR-08 sanity review
(:func:`revalid.sanity.review_verdict`) are both deterministic and take no input
beyond the evidence. So every persisted verdict can be *re-derived from the audit
trail alone* — its stored evidence — with no probe re-execution.

:func:`rederive_run` recomputes every stored verdict and diffs it against
storage. A clean run proves reproducibility (FR-10 acceptance / NFR-02); any
discrepancy flags a verdict whose assessment logic has since changed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.db import VerdictRecord
from revalid.domain import Evidence, Verdict
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


def rederive_run(session: Session) -> AuditReport:
    """Re-derive every stored verdict from its evidence and diff against storage.

    Reproduces each verdict from the audit trail alone (FR-10 AC): no probe is
    re-executed — the only input is each row's persisted evidence. Returns counts
    and any discrepancies (empty when the trail fully reproduces the verdicts).
    """
    records = list(session.scalars(select(VerdictRecord).order_by(VerdictRecord.id)))
    discrepancies: list[Discrepancy] = []
    for record in records:
        stored = record.to_domain()
        rederived = rederive_verdict(record.probe_kind, stored.evidence)
        if rederived != stored:
            discrepancies.append(
                Discrepancy(
                    verdict_id=record.id,
                    finding_id=record.finding_id,
                    stored=f"{stored.status.value}/{stored.reason_code}",
                    rederived=f"{rederived.status.value}/{rederived.reason_code}",
                )
            )
    return AuditReport(
        total=len(records),
        reproduced=len(records) - len(discrepancies),
        discrepancies=tuple(discrepancies),
    )
