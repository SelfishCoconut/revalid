"""Server-side retest-plan approval gate and versioning (FR-05, ADR-0012).

Plans are inert until approved. This module owns the plan lifecycle — propose
(from FR-04 generation or a user edit) → approve / reject, with older versions
superseded — and the *single* execution chokepoint. Nothing runs a plan except
:func:`execute_approved_plan`, which refuses unless the finding has an
``approved`` version (FR-05 AC1). Edited actions are re-gated through the same
FR-06 allowlist gate as generated ones, so an edit cannot escape the allowlist.
"""

from __future__ import annotations

import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from revalid.allowlist import TargetGuard
from revalid.browser import BrowserProbeUnavailableError, BrowserRunner, is_browser_probe
from revalid.db import PlanRecord, VerdictRecord
from revalid.domain import PlanStatus, Probe, RetestPlan, Verdict
from revalid.plan import PlannedAction, PlanResult, RejectedAction, gate_actions
from revalid.retest import assess_evidence, classify_kind_from_text, run_probe
from revalid.sanity import guarded_run

_ACTOR = "user"


class NoProposedPlanError(Exception):
    """Raised when approve/reject is attempted with no proposed plan."""

    def __init__(self, finding_id: int) -> None:
        """Store the ``finding_id`` that has no proposed plan."""
        super().__init__(f"finding {finding_id} has no proposed plan")
        self.finding_id = finding_id


class AllActionsRejectedError(Exception):
    """Raised when every edited action is dropped by the gate (nothing to run)."""

    def __init__(self, finding_id: int, rejected: list[RejectedAction]) -> None:
        """Store the ``finding_id`` and the ``rejected`` actions that caused this."""
        super().__init__(f"all edited actions for finding {finding_id} were rejected")
        self.finding_id = finding_id
        self.rejected = rejected


class PlanNotApprovedError(Exception):
    """Raised when execution is attempted without an approved plan (AC1)."""

    def __init__(self, finding_id: int) -> None:
        """Store the ``finding_id`` that has no approved plan."""
        super().__init__(f"finding {finding_id} has no approved plan")
        self.finding_id = finding_id


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _proposed_rows(session: Session, finding_id: int) -> list[PlanRecord]:
    return list(
        session.scalars(
            select(PlanRecord).where(
                PlanRecord.finding_id == finding_id,
                PlanRecord.status == PlanStatus.PROPOSED.value,
            )
        )
    )


def _next_version(session: Session, finding_id: int) -> int:
    versions = session.scalars(
        select(PlanRecord.version).where(PlanRecord.finding_id == finding_id)
    ).all()
    return max(versions) + 1 if versions else 1


def _persist_proposed(
    session: Session,
    finding_id: int,
    plan: RetestPlan,
    origin: str,
    rejected: list[RejectedAction],
) -> PlanRecord:
    """Supersede any live proposal and insert a new proposed version."""
    for row in _proposed_rows(session, finding_id):
        row.status = PlanStatus.SUPERSEDED.value
    record = PlanRecord.from_plan(
        finding_id,
        plan,
        version=_next_version(session, finding_id),
        status=PlanStatus.PROPOSED,
        origin=origin,
        rejected_actions=[r.model_dump() for r in rejected],
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def save_generated_plan(session: Session, finding_id: int, result: PlanResult) -> PlanRecord:
    """Persist an FR-04 generation result as a new proposed version."""
    return _persist_proposed(session, finding_id, result.plan, "generated", list(result.rejected))


def edit_plan(
    session: Session,
    finding_id: int,
    actions: list[PlannedAction],
    guard: TargetGuard,
    base_url: str,
    *,
    finding_title: str,
) -> tuple[PlanRecord, list[RejectedAction]]:
    """Re-gate user-edited actions and persist them as a new proposed version.

    ``finding_title`` is supplied by the caller (the endpoint already loaded the
    finding for its 404 check), keeping this function free of the existence check.

    Raises:
        AllActionsRejectedError: If no submitted action survives the gate.
    """
    probes, rejected = gate_actions(
        actions, guard, base_url, classify_kind_from_text(finding_title)
    )
    if not probes:
        raise AllActionsRejectedError(finding_id, rejected)
    plan = RetestPlan(
        finding_title=finding_title,
        actions=tuple(probes),
        raw={
            "source": "plan_edit",
            "base_url": base_url,
            "finding_title": finding_title,
            "proposed": len(actions),
            "rejected": len(rejected),
        },
    )
    return _persist_proposed(session, finding_id, plan, "edited", rejected), rejected


def approve_plan(session: Session, finding_id: int, actor: str = _ACTOR) -> PlanRecord:
    """Approve the latest proposed version, superseding any prior approved one."""
    proposed = _latest_proposed(session, finding_id)
    if proposed is None:
        raise NoProposedPlanError(finding_id)
    current = approved_plan(session, finding_id)
    if current is not None:
        current.status = PlanStatus.SUPERSEDED.value
    proposed.status = PlanStatus.APPROVED.value
    proposed.decided_at = _now()
    proposed.decided_by = actor
    session.commit()
    session.refresh(proposed)
    return proposed


def reject_plan(session: Session, finding_id: int, actor: str = _ACTOR) -> PlanRecord:
    """Reject the latest proposed version."""
    proposed = _latest_proposed(session, finding_id)
    if proposed is None:
        raise NoProposedPlanError(finding_id)
    proposed.status = PlanStatus.REJECTED.value
    proposed.decided_at = _now()
    proposed.decided_by = actor
    session.commit()
    session.refresh(proposed)
    return proposed


def _latest_proposed(session: Session, finding_id: int) -> PlanRecord | None:
    return session.scalars(
        select(PlanRecord)
        .where(
            PlanRecord.finding_id == finding_id,
            PlanRecord.status == PlanStatus.PROPOSED.value,
        )
        .order_by(PlanRecord.version.desc())
    ).first()


def approved_plan(session: Session, finding_id: int) -> PlanRecord | None:
    """Return the single approved plan version for a finding, or ``None``."""
    return session.scalars(
        select(PlanRecord).where(
            PlanRecord.finding_id == finding_id,
            PlanRecord.status == PlanStatus.APPROVED.value,
        )
    ).first()


def list_plans(session: Session, finding_id: int) -> list[PlanRecord]:
    """Return all plan versions for a finding, oldest first."""
    return list(
        session.scalars(
            select(PlanRecord)
            .where(PlanRecord.finding_id == finding_id)
            .order_by(PlanRecord.version)
        )
    )


def _probe_verdict(
    probe: Probe, client: httpx.Client, browser_runner: BrowserRunner | None
) -> Verdict:
    """Run one probe through the transport its kind requires (FR-07 HTTP / FR-14 browser).

    Both paths converge on the shared :func:`~revalid.retest.assess_evidence`, so a
    browser verdict is assessed and re-derived (FR-10) exactly like an HTTP one.

    Raises:
        BrowserProbeUnavailableError: If a browser probe runs without the extra.
    """
    if is_browser_probe(probe):
        if browser_runner is None:
            raise BrowserProbeUnavailableError()
        return assess_evidence(probe.kind, browser_runner(probe))
    return run_probe(client, probe)


def execute_approved_plan(
    session: Session,
    client: httpx.Client,
    finding_id: int,
    *,
    browser_runner: BrowserRunner | None = None,
) -> list[VerdictRecord]:
    """Run the approved plan's probes; the ONLY path from storage to the network.

    Each probe runs through the FR-08 :func:`~revalid.sanity.guarded_run` verifier
    (ADR-0014): an off-plan probe is blocked before any request, and an
    over-confident *fixed* verdict is downgraded to *inconclusive*. Browser-kind
    probes (FR-14) run via ``browser_runner`` under the identical guard; HTTP
    probes use ``client``.

    Raises:
        PlanNotApprovedError: If the finding has no approved plan version (AC1).
        PlanDeviationError: If a probe not in the approved plan reaches execution
            (FR-08 AC1) — fail-closed; the run aborts.
        BrowserProbeUnavailableError: If a browser probe is approved but the
            Playwright extra is not installed.
    """
    plan = approved_plan(session, finding_id)
    if plan is None:
        raise PlanNotApprovedError(finding_id)
    approved = plan.probes()
    execute = lambda probe: _probe_verdict(probe, client, browser_runner)  # noqa: E731
    records: list[VerdictRecord] = []
    for probe in approved:
        verdict = guarded_run(probe, approved, execute)
        records.append(
            VerdictRecord.from_domain(
                finding_id, probe.kind, verdict, plan_id=plan.id, plan_version=plan.version
            )
        )
    session.add_all(records)
    session.commit()
    for record in records:
        session.refresh(record)
    return records
