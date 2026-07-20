"""FastAPI application factory (ADR-0002: local single-user web app).

Run locally with::

    uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1

The app must only ever bind to 127.0.0.1 (NFR-03); there is no
authentication in TFG scope.
"""

import asyncio
import contextlib
import hashlib
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, DeferredToolRequests
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from revalid import __version__
from revalid.audit import rederive_run
from revalid.db import (
    FindingNoteRecord,
    FindingRecord,
    FindingVersionRecord,
    ReportRecord,
    RetestSessionRecord,
    SessionEventRecord,
    VerdictRecord,
    create_db_engine,
    session_factory,
)
from revalid.domain import (
    AgenticEvidence,
    Finding,
    FindingStage,
    ReportStatus,
    RetestSessionStatus,
    SessionEventKind,
    Settings,
    Severity,
    VerdictStatus,
)
from revalid.export import RunExport, build_export, export_schema
from revalid.extract import (
    ExtractedFinding,
    ReportMetadata,
    build_extraction_agent,
    build_metadata_agent,
    extract_metadata,
    extract_report,
)
from revalid.findings import (
    add_note,
    add_version,
    create_finding,
    current_version,
    list_notes,
    list_versions,
)
from revalid.ingest import IngestError, map_defectdojo_export
from revalid.llm import agent_model_name, build_model
from revalid.pdf import PdfError, read_pdf
from revalid.plan import (
    GeneratedGoal,
    build_goal_agent,
    generate_goal,
)
from revalid.retest_agent import (
    ConcludeOutput,
    RetestSessionDeps,
    build_qa_agent,
    build_retest_agent,
)
from revalid.retest_session import (
    SessionRegistry,
    _fail,
    adjudicate_verdict,
    answer_operator_question,
    append_event,
    apply_decision,
    conclude_session,
    continue_session,
    create_session,
    end_session,
    is_terminal,
    load_events_after,
    set_free_launch,
    set_goal,
    start_and_step,
    submit_human_command,
    submit_message,
)
from revalid.sandbox import DockerSandbox, Sandbox, SandboxFactory
from revalid.settings import ProbeResult, load_or_seed, probe_provider, save

#: The retest agent's static output type: a verdict, or a deferred approval request.
RetestAgent = Agent[RetestSessionDeps, ConcludeOutput | DeferredToolRequests]

# Repo-root-relative location of the built SPA (frontend/dist); served at "/"
# when present (FR-11). Absent in backend-only dev and CI unit runs.
_SPA_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class FindingOut(Finding):
    """A persisted finding as returned by the API — the *current* version's content.

    ``version`` is the current version number (extraction = 1); it bumps on every
    operator edit (FR-16). ``id`` is the stable finding identity verdicts and
    retest sessions reference.
    """

    id: int
    report_id: int | None = None
    version: int = 1


class FindingVersionOut(Finding):
    """One immutable finding version as returned by the API (FR-16)."""

    version: int
    origin: str
    edited_by: str | None = None
    reason: str = ""
    created_at: datetime

    @classmethod
    def from_record(cls, record: FindingVersionRecord) -> "FindingVersionOut":
        """Build the API view from a persisted finding-version row."""
        return cls(
            version=record.version,
            origin=record.origin,
            edited_by=record.edited_by,
            reason=record.reason,
            created_at=record.created_at,
            **record.to_domain().model_dump(),
        )


class FindingEditIn(BaseModel):
    """An operator edit of a finding's content → a new immutable version (FR-16)."""

    title: str = Field(min_length=1)
    severity: Severity
    description: str = ""
    impact: str = ""
    attack_vector: str = ""
    affected_endpoints: tuple[str, ...] = ()
    reproduction_steps: tuple[str, ...] = ()
    reason: str = ""

    def to_finding(self, base_raw: dict[str, Any]) -> Finding:
        """Build the domain finding for this edit, carrying forward prior lineage.

        ``base_raw`` is the current version's ``raw`` (extraction lineage), kept so
        editing never discards how the finding was originally produced (FR-10).
        """
        return Finding(
            title=self.title,
            severity=self.severity,
            description=self.description,
            impact=self.impact,
            attack_vector=self.attack_vector,
            affected_endpoints=self.affected_endpoints,
            reproduction_steps=self.reproduction_steps,
            raw=base_raw,
        )


class NoteIn(BaseModel):
    """An operator note on a finding, tagged with the stage it was written on (FR-16)."""

    stage: FindingStage = FindingStage.GENERAL
    body: str = Field(min_length=1)


class NoteOut(BaseModel):
    """A persisted finding note as returned by the API (FR-16)."""

    id: int
    finding_id: int
    stage: str
    body: str
    author: str
    created_at: datetime

    @classmethod
    def from_record(cls, record: FindingNoteRecord) -> "NoteOut":
        """Build the API view from a persisted note row."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            stage=record.stage,
            body=record.body,
            author=record.author,
            created_at=record.created_at,
        )


class VerdictOut(BaseModel):
    """An agentic verdict as returned by the API (FR-09/FR-17).

    Every verdict is a retest-session conclusion (the batch verdict path retired
    in FR-17 6b-iii); the shape mirrors the FR-12 :class:`~revalid.export.VerdictExport`.
    """

    id: int
    finding_id: int
    session_id: int | None
    actor: str
    status: VerdictStatus
    reason_code: str
    rationale: str
    matched_indicators: tuple[str, ...]
    evidence: AgenticEvidence | None

    @classmethod
    def from_record(cls, record: VerdictRecord) -> "VerdictOut":
        """Build the API view from a stored verdict row."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            session_id=record.session_id,
            actor=record.actor,
            status=VerdictStatus(record.status),
            reason_code=record.reason_code,
            rationale=record.rationale,
            matched_indicators=tuple(record.matched_indicators),
            evidence=AgenticEvidence(**record.evidence) if record.evidence is not None else None,
        )


class DiscrepancyOut(BaseModel):
    """A stored verdict that no longer re-derives from its evidence (FR-10)."""

    verdict_id: int
    finding_id: int
    stored: str
    rederived: str


class AuditOut(BaseModel):
    """Result of re-deriving every verdict from the stored audit trail (FR-10)."""

    total: int
    reproduced: int
    ok: bool
    discrepancies: list[DiscrepancyOut]


class SessionEventOut(BaseModel):
    """One append-only transcript event as returned by the API (FR-17)."""

    seq: int
    kind: str
    payload: dict[str, Any]


class RetestSessionOut(BaseModel):
    """A persisted agentic retest session + its transcript as returned by the API (FR-17)."""

    id: int
    finding_id: int
    status: str
    model: str
    verdict_status: str | None
    verdict_rationale: str | None
    free_launch: bool
    events: list[SessionEventOut] = []

    @classmethod
    def from_record(
        cls, record: RetestSessionRecord, events: list[dict[str, Any]]
    ) -> "RetestSessionOut":
        """Build the API view from a session row and its ordered transcript events."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            status=record.status,
            model=record.model,
            verdict_status=record.verdict_status,
            verdict_rationale=record.verdict_rationale,
            free_launch=record.free_launch,
            events=[SessionEventOut(**e) for e in events],
        )


class RetestSessionSummary(BaseModel):
    """A compact retest-session row for a finding's session list (FR-17 6b-iii-b)."""

    id: int
    finding_id: int
    status: str
    verdict_status: str | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: RetestSessionRecord) -> "RetestSessionSummary":
        """Build the compact list-row view from a full session record."""
        return cls(
            id=record.id,
            finding_id=record.finding_id,
            status=record.status,
            verdict_status=record.verdict_status,
            created_at=record.created_at,
        )


class RejectRequest(BaseModel):
    """Optional body for a command rejection: the operator's reason (FR-17)."""

    reason: str = ""


class HumanCommandRequest(BaseModel):
    """Body for a manual operator command (`!`): the exact command to run (FR-17)."""

    command: str = Field(min_length=1)


class MessageRequest(BaseModel):
    """Body for an operator chat message to the agent (FR-17 Slice 4)."""

    text: str = Field(min_length=1)


class StartSessionRequest(BaseModel):
    """Optional body for starting a session: free-launch + seed goal (FR-17 Slice 5)."""

    free_launch: bool = False
    # A user-owned goal drafted before the session (FR-17 6b-iii-b): when present,
    # the session seeds it verbatim instead of generating one at start.
    initial_goal: list[str] | None = None
    # The retest scope — the exact target URL(s) the agent may hit (FR-17). Set at
    # launch (reachability is fixed when the sandbox is provisioned), so there is no
    # live-edit path; changing scope means a fresh session. Defaults to the finding's
    # affected endpoints when omitted.
    target_endpoints: list[str] | None = None


class FreeLaunchRequest(BaseModel):
    """Body for the live free-launch toggle (FR-17 Slice 5)."""

    enabled: bool


class GoalRequest(BaseModel):
    """Body for a user-owned goal edit (FR-17 6b-ii)."""

    steps: list[str]


class GoalDraftOut(BaseModel):
    """A generated retest-goal draft for a finding, pre-session (FR-17 6b-iii-b)."""

    steps: list[str]


class AdjudicateRequest(BaseModel):
    """Body for a human verdict adjudication of a concluded session (FR-17 Slice 6a).

    ``status`` is the human's call — equal to the agent's when accepting, or a
    different value when overriding; ``rationale`` is their justification.
    """

    status: VerdictStatus
    rationale: str = ""


class ConcludeRequest(BaseModel):
    """Body for an operator's manual conclusion of a paused session (ADR-0034)."""

    status: VerdictStatus
    rationale: str = ""


class ImportResult(BaseModel):
    """Outcome of a findings import."""

    imported: int


class ReportOut(BaseModel):
    """A persisted report / ingest job as returned by the API (FR-01/FR-11)."""

    id: int
    filename: str
    status: str
    model: str
    error: str | None
    finding_count: int
    archived: bool
    content_hash: str | None
    metadata: ReportMetadata | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: ReportRecord) -> "ReportOut":
        """Build the API view from a persisted report row."""
        return cls(
            id=record.id,
            filename=record.filename,
            status=record.status,
            model=record.model,
            error=record.error,
            finding_count=record.finding_count,
            archived=record.archived,
            content_hash=record.content_hash,
            metadata=(
                ReportMetadata.model_validate(record.doc_metadata) if record.doc_metadata else None
            ),
            created_at=record.created_at,
        )


class ReportPatchIn(BaseModel):
    """Body for archiving / unarchiving a report (FR-11, #128)."""

    archived: bool


class BackendStatusOut(BaseModel):
    """Live LLM-backend reachability + active model, for the sidebar status pill."""

    model_config = ConfigDict(protected_namespaces=())

    connected: bool
    model: str


class SettingsOut(BaseModel):
    """Public view of the model/provider setting; the key is write-only (ADR-0021)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    base_url: str | None
    api_key_set: bool
    api_key_hint: str | None

    @classmethod
    def from_domain(cls, cfg: Settings) -> "SettingsOut":
        """Build the masked view: the key becomes a boolean + last-4 hint only."""
        key = cfg.api_key or ""
        return cls(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key_set=bool(key),
            api_key_hint=key[-4:] if key else None,
        )


class SettingsUpdateIn(BaseModel):
    """Settings update payload; a blank ``api_key`` keeps the stored one (ADR-0021)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    clear_key: bool = False


class ProbeIn(BaseModel):
    """Probe request: which endpoint (and optional key) to discover models from."""

    base_url: str | None = None
    api_key: str | None = None


def get_settings_dep(request: Request) -> Settings:
    """Load the persisted model/provider setting, seeding a fresh DB (ADR-0021)."""
    sessions = cast("sessionmaker[Session]", request.app.state.sessions)
    with sessions() as session:
        return load_or_seed(session)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_goal_agent(settings: SettingsDep) -> Agent[None, GeneratedGoal]:
    """Yield the FR-17 retest-goal agent built from the persisted setting (ADR-0021)."""
    return build_goal_agent(build_model(settings))


def get_extraction_agent(settings: SettingsDep) -> Agent[None, list[ExtractedFinding]]:
    """Yield the FR-03 extraction agent built from the persisted setting (ADR-0021)."""
    return build_extraction_agent(build_model(settings))


def get_metadata_agent(settings: SettingsDep) -> Agent[None, ReportMetadata]:
    """Yield the FR-03 document-metadata agent built from the persisted setting (#133)."""
    return build_metadata_agent(build_model(settings))


def get_retest_agent(settings: SettingsDep) -> RetestAgent:
    """Yield the FR-17 agentic retest agent built from the persisted setting (ADR-0021)."""
    return build_retest_agent(build_model(settings))


def get_qa_agent(settings: SettingsDep) -> Agent[None, str]:
    """Yield the FR-17 chat Q&A agent built from the persisted setting (ADR-0021)."""
    return build_qa_agent(build_model(settings))


def get_sandbox_factory() -> SandboxFactory:
    """Yield the production sandbox factory: a fresh egress-locked Docker sandbox per session.

    The returned factory is bound to the retest session's id (which scopes the
    sandbox's internal Docker network, FR-06). Tests override this with a factory
    that returns a :class:`~revalid.sandbox.FakeSandbox`, so the HTTP flow runs
    without Docker.
    """
    return lambda sid: DockerSandbox(sid)


# Module-level dependency aliases (no per-app-instance state) — unlike SessionDep,
# which closes over each app's own session factory.
GoalAgentDep = Annotated[Agent[None, GeneratedGoal], Depends(get_goal_agent)]
ExtractionAgentDep = Annotated[Agent[None, list[ExtractedFinding]], Depends(get_extraction_agent)]
MetadataAgentDep = Annotated[Agent[None, ReportMetadata], Depends(get_metadata_agent)]
RetestAgentDep = Annotated[RetestAgent, Depends(get_retest_agent)]
QaAgentDep = Annotated[Agent[None, str], Depends(get_qa_agent)]
SandboxFactoryDep = Annotated[SandboxFactory, Depends(get_sandbox_factory)]


def run_extraction(
    sessions: sessionmaker[Session],
    report_id: int,
    data: bytes,
    agent: Agent[None, list[ExtractedFinding]],
    metadata_agent: Agent[None, ReportMetadata],
) -> None:
    """Extract findings from an uploaded PDF and persist them (FR-01/FR-03/FR-11).

    Runs as a FastAPI background task — a sync function Starlette dispatches to
    its threadpool, so it must open its **own** session (the request session is
    already closed once the ``202`` was sent) and it never blocks the event
    loop. The report is always moved out of ``extracting``: to ``ready`` with
    its findings persisted, or ``failed`` with the error recorded — so the UI's
    status poll always terminates.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        report_id: The ``extracting`` report row to fill in.
        data: The uploaded PDF bytes.
        agent: The extraction agent (a stand-in model in tests).
        metadata_agent: The document-metadata agent (a stand-in model in tests).
    """
    with sessions() as session:
        report = session.get(ReportRecord, report_id)
        if report is None:  # pragma: no cover - the row was just committed
            return
        try:
            pdf = read_pdf(data)
            result = extract_report(agent, pdf)
        except PdfError as exc:
            report.status, report.error = ReportStatus.FAILED.value, str(exc)
            session.commit()
            return
        except Exception as exc:
            report.status, report.error = ReportStatus.FAILED.value, f"extraction failed: {exc}"
            session.commit()
            return
        _persist_findings(session, result.findings, report_id=report_id)
        # Best-effort document metadata (#133) — never fails the report.
        report.doc_metadata = extract_metadata(metadata_agent, pdf).model_dump()
        report.status = ReportStatus.READY.value
        report.finding_count = len(result.findings)
        session.commit()


def _get_finding_or_404(session: Session, finding_id: int) -> FindingRecord:
    """Return the finding identity row, or raise the shared 404 (FR-05 endpoints)."""
    finding = session.get(FindingRecord, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


def _current_or_404(session: Session, finding_id: int) -> FindingVersionRecord:
    """Return the finding's current version, or raise the shared 404 (FR-16).

    A finding always has a version (created together), so a missing current
    version means the finding itself is absent.
    """
    version = current_version(session, finding_id)
    if version is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return version


def _session_finding(session: Session, session_id: int) -> Finding | None:
    """Return the current finding for a session, or ``None`` if unavailable (FR-17).

    Gives the chat Q&A its context. Tolerant of a missing session or finding version
    so sending a message never 404s — the reply is simply skipped when there is no
    finding to reason from.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return None
    with contextlib.suppress(HTTPException):
        return _current_or_404(session, record.finding_id).to_domain()
    return None


def _finding_out(identity: FindingRecord, version: FindingVersionRecord) -> FindingOut:
    """Assemble the API finding view from its identity + current version (FR-16)."""
    return FindingOut(
        id=identity.id,
        report_id=identity.report_id,
        version=version.version,
        **version.to_domain().model_dump(),
    )


def _persist_findings(
    session: Session, findings: Iterable[Finding], report_id: int | None = None
) -> None:
    """Create identity + version-1 rows for a batch of newly ingested findings (FR-16).

    Shared by extraction, FR-02 import, and manual entry — each lands every finding
    as its ``extraction`` version 1 (ADR-0024). The caller owns the ``commit``.
    """
    for finding in findings:
        create_finding(session, finding, report_id=report_id)


def run_first_step(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    agent: RetestAgent,
    make_sandbox: SandboxFactory,
    finding: Finding,
    goal_agent: Agent[None, GeneratedGoal],
    initial_goal: tuple[str, ...] | None = None,
    target_endpoints: tuple[str, ...] | None = None,
) -> None:
    """Build the sandbox and run the retest agent's first step (FR-17 background task).

    Runs as a FastAPI background task — a sync function on Starlette's threadpool
    — so it opens its **own** session (the request session closed with the
    ``202``). It must let **no** exception escape: ``make_sandbox`` may raise
    (``SandboxUnavailableError`` when the ``sandbox`` extra is absent, or a real
    Docker error) and :func:`~revalid.retest_session.start_and_step` calls
    ``sandbox.start()`` outside its own guard, so both are wrapped here. On any
    failure the sandbox (if one was created) is best-effort torn down and the
    session is settled to ``error`` — never stranded in ``starting``.

    It also **seeds the goal** (FR-17 6b-ii): a caller-supplied ``initial_goal``
    (a goal drafted before the session started, FR-17 6b-iii-b) is used verbatim;
    otherwise the goal agent generates a generic retest goal. Either way the
    result is emitted as the initial ``plan_updated`` (the "Current goal" panel)
    and prepended to the agent's prompt. Goal generation is best-effort — a
    failure degrades to an empty goal, never blocking start.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The already-created (``starting``) retest session to drive.
        agent: The built retest agent (a stand-in model in tests).
        make_sandbox: The session-scoped sandbox factory.
        finding: The finding to retest — the agent's goal is derived from it.
        goal_agent: The FR-17 goal agent (a stand-in model in tests).
        initial_goal: A pre-start goal drafted by the user (FR-17 6b-iii-b); when
            non-empty it is seeded verbatim and generation is skipped.
        target_endpoints: The launch-time retest scope (FR-17) — the exact URL(s)
            the agent may hit; defaults to the finding's endpoints when omitted.
    """
    with sessions() as session:
        sandbox: Sandbox | None = None
        try:
            sandbox = make_sandbox(session_id)
            record = session.get(RetestSessionRecord, session_id)
            free_launch = record.free_launch if record else False
            # Scope is set once, at launch: the operator's endpoints when supplied,
            # else the finding's. It's recorded (TARGET_SET) for the read-only cockpit
            # display and injected authoritatively into the prompt.
            endpoints = tuple(target_endpoints) if target_endpoints else finding.affected_endpoints
            if endpoints:
                append_event(
                    session, session_id, SessionEventKind.TARGET_SET, {"endpoints": list(endpoints)}
                )
            goal: tuple[str, ...] = tuple(initial_goal) if initial_goal else ()
            if not goal:  # no pre-start draft → generate (best-effort; never blocks)
                with contextlib.suppress(Exception):
                    goal = generate_goal(goal_agent, finding)
            if goal:
                append_event(
                    session, session_id, SessionEventKind.PLAN_UPDATED, {"steps": list(goal)}
                )
            start_and_step(
                session,
                registry,
                session_id,
                agent,
                sandbox,
                _target_preamble(endpoints) + _goal_prompt(goal, finding),
                free_launch=free_launch,
            )
        except Exception as exc:  # broad on purpose: no failure may strand the session
            if sandbox is not None:
                with contextlib.suppress(Exception):  # best-effort teardown only
                    sandbox.stop()
            _fail(session, registry, session_id, str(exc))


def run_decision(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    approved: bool,
    reason: str,
    command_id: str,
) -> None:
    """Resume a paused session with the operator's decision (FR-17 background task).

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The retest session to resume.
        approved: Whether the pending command was approved.
        reason: Optional operator reason (surfaced to the model on rejection).
        command_id: The ``cid`` path param from the approve/reject URL; must
            match the session's pending ``tool_call_id`` or the decision is a
            no-op (guards against a double-click resuming the run twice).
    """
    with sessions() as session:
        apply_decision(
            session, registry, session_id, approved=approved, reason=reason, command_id=command_id
        )


def run_human_command(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    command: str,
) -> None:
    """Run a manual operator command (`!`) in the session's sandbox (FR-17 background task).

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The retest session to run the command in.
        command: The exact shell command the operator submitted (without the `!`).
    """
    with sessions() as session:
        submit_human_command(session, registry, session_id, command)


def run_goal(
    sessions: sessionmaker[Session], registry: SessionRegistry, session_id: int, steps: list[str]
) -> None:
    """Set the user-owned goal on a session (FR-17 6b-ii background task)."""
    with sessions() as session:
        set_goal(session, registry, session_id, steps)


def run_regenerate_goal(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    goal_agent: Agent[None, GeneratedGoal],
    finding: Finding,
) -> None:
    """Regenerate + set the goal for a session (FR-17 6b-ii background task)."""
    with sessions() as session:
        set_goal(session, registry, session_id, list(generate_goal(goal_agent, finding)))


def run_message(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    text: str,
    qa_agent: Agent[None, str],
    finding: Finding | None,
) -> None:
    """Queue an operator chat message and answer it immediately (FR-17 background task).

    Two things happen, both additive: the message is buffered for the main agent's
    next turn (steering, as before), AND the operator gets an immediate chat reply
    from a read-only Q&A over the transcript. The reply is decoupled from the
    deferred-command loop, so asking a question never disturbs a pending command.
    Best-effort — a Q&A failure never strands the (already-recorded) message.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The retest session to message.
        text: The exact operator message.
        qa_agent: The prose Q&A agent (a stand-in model in tests).
        finding: The session's finding, for Q&A context; ``None`` skips the reply.
    """
    with sessions() as session:
        submit_message(session, registry, session_id, text)
        # Only reply when the message was actually recorded (session live) and we
        # have finding context; answer_operator_question reads events + finding and
        # appends an agent_message that the transcript stream surfaces.
        if finding is not None and registry.get(session_id) is not None:
            with contextlib.suppress(Exception):
                answer = answer_operator_question(qa_agent, session, session_id, finding, text)
                if answer:
                    append_event(
                        session, session_id, SessionEventKind.AGENT_MESSAGE, {"text": answer}
                    )


def run_free_launch(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    enabled: bool,
) -> None:
    """Toggle free-launch on a session (FR-17 Slice 5 background task).

    Runs in the background because enabling may drive the auto-approve loop
    (successive agent turns). A no-op if the session is no longer live.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The retest session to toggle.
        enabled: The new free-launch state.
    """
    with sessions() as session:
        set_free_launch(session, registry, session_id, enabled)


def run_continue(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
) -> None:
    """Resume a paused session (ADR-0034 "Keep going", background task).

    Runs in the background because resuming drives further agent turns. A no-op
    unless the session is paused in ``needs_guidance`` with a live agent.

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The paused retest session to resume.
    """
    with sessions() as session:
        continue_session(session, registry, session_id)


def run_conclude(
    sessions: sessionmaker[Session],
    registry: SessionRegistry,
    session_id: int,
    status: VerdictStatus,
    rationale: str,
) -> None:
    """Record the operator's manual conclusion + tear down (ADR-0034 background task).

    Args:
        sessions: The app's session factory (each task opens a fresh session).
        registry: The process-local live-session registry.
        session_id: The session to conclude.
        status: The operator's determination.
        rationale: The operator's justification.
    """
    with sessions() as session:
        conclude_session(session, registry, session_id, status, rationale)


def _finding_prompt(finding: Finding) -> str:
    """Render the finding as the retest agent's goal prompt (FR-17).

    Gives the agent the finding's identity and how it was originally reproduced so
    it can re-verify the issue against the lab target. Mirrors the FR-04 planning
    prompt (:mod:`revalid.plan`) but carries no operator steering — the retest
    agent proposes each command for human approval instead.
    """
    lines = [f"Title: {finding.title}", f"Description: {finding.description}"]
    if finding.affected_endpoints:
        lines.append("Affected endpoints: " + ", ".join(finding.affected_endpoints))
    if finding.reproduction_steps:
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(finding.reproduction_steps, 1))
        lines.append(f"Reproduction steps:\n{steps}")
    return "\n".join(lines)


def _goal_prompt(goal: tuple[str, ...], finding: Finding) -> str:
    """Prepend the current goal (if any) to the finding context for the agent (FR-17 6b-ii)."""
    base = _finding_prompt(finding)
    if not goal:
        return base
    steps = "\n".join(f"- {s}" for s in goal)
    return f"Current goal:\n{steps}\n\n{base}"


def _target_preamble(endpoints: tuple[str, ...]) -> str:
    """An authoritative scope line for the agent: the exact target URLs to hit (FR-17).

    Prepended to the prompt at launch so a weaker model cannot invent or substitute
    a hostname — it must send every request to one of these operator-set URLs.
    Empty string when no scope was set, so callers can prepend unconditionally.
    """
    if not endpoints:
        return ""
    urls = "\n".join(f"- {e}" for e in endpoints)
    return (
        "Target endpoints — send every request ONLY to these exact URLs; do not "
        f"invent, guess, or substitute a hostname:\n{urls}\n\n"
    )


def _register_core_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register ``/health`` and the FR-02 finding import/list routes.

    Split from :func:`_register_plan_routes` purely to keep each
    registration function's route-closure count under the mccabe gate (each
    nested route def adds to its enclosing function's measured complexity).
    Each registration function binds its own ``SessionDep``, closing over this
    app's ``sessions`` factory, rather than sharing one across functions. Routes
    are registered on the ``/api`` router (FR-11): the SPA owns the root paths.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/health")
    def health() -> dict[str, str]:
        """Liveness check."""
        return {"status": "ok", "version": __version__}

    @router.post("/findings/import", response_model=ImportResult)
    def import_findings(payload: dict[str, Any], session: SessionDep) -> ImportResult:
        """Ingest a DefectDojo-style JSON export (FR-02)."""
        try:
            findings = map_defectdojo_export(payload)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _persist_findings(session, findings)
        session.commit()
        return ImportResult(imported=len(findings))

    @router.get("/findings", response_model=list[FindingOut])
    def list_findings(session: SessionDep, report_id: int | None = None) -> list[FindingOut]:
        """List persisted findings (current version each), optionally by report (FR-11)."""
        stmt = select(FindingRecord).order_by(FindingRecord.id)
        if report_id is not None:
            stmt = stmt.where(FindingRecord.report_id == report_id)
        out: list[FindingOut] = []
        for identity in session.scalars(stmt):
            version = current_version(session, identity.id)
            if version is None:  # pragma: no cover - a finding always has >=1 version
                continue
            out.append(_finding_out(identity, version))
        return out


def _register_finding_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-16 finding-revision (versioned edit) and annotation routes.

    Split from :func:`_register_core_routes` to keep each registration function's
    nested-route count under the mccabe gate (see that function's note). Editing a
    finding appends an immutable version; notes are an append-only, stage-tagged
    log — nothing here mutates or deletes prior history (ADR-0024).
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}", response_model=FindingOut)
    def edit_finding(finding_id: int, body: FindingEditIn, session: SessionDep) -> FindingOut:
        """Record an operator edit as a new immutable finding version (FR-16)."""
        current = _current_or_404(session, finding_id)
        identity = _get_finding_or_404(session, finding_id)
        version = add_version(session, finding_id, body.to_finding(current.raw), reason=body.reason)
        return _finding_out(identity, version)

    @router.get("/findings/{finding_id}/versions", response_model=list[FindingVersionOut])
    def get_finding_versions(finding_id: int, session: SessionDep) -> list[FindingVersionOut]:
        """List every version of a finding, oldest first — extraction = v1 (FR-16)."""
        _current_or_404(session, finding_id)
        return [FindingVersionOut.from_record(r) for r in list_versions(session, finding_id)]

    @router.post("/findings/{finding_id}/notes", response_model=NoteOut, status_code=201)
    def add_finding_note(finding_id: int, body: NoteIn, session: SessionDep) -> NoteOut:
        """Append a stage-tagged note to the finding's log (FR-16)."""
        _get_finding_or_404(session, finding_id)
        return NoteOut.from_record(add_note(session, finding_id, body.stage, body.body))

    @router.get("/findings/{finding_id}/notes", response_model=list[NoteOut])
    def get_finding_notes(finding_id: int, session: SessionDep) -> list[NoteOut]:
        """List a finding's notes, newest first (FR-16)."""
        _get_finding_or_404(session, finding_id)
        return [NoteOut.from_record(r) for r in list_notes(session, finding_id)]


def _register_finding_retest_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the finding-level agentic-retest helpers (goal draft + session list).

    FR-17 6b-iii-b. Kept out of ``_register_finding_routes`` to stay under the mccabe gate.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/findings/{finding_id}/goal/draft", response_model=GoalDraftOut)
    def draft_goal(finding_id: int, session: SessionDep, goal_agent: GoalAgentDep) -> GoalDraftOut:
        """Generate a retest-goal draft for the finding — no session, no persistence."""
        finding = _current_or_404(session, finding_id).to_domain()
        return GoalDraftOut(steps=list(generate_goal(goal_agent, finding)))

    @router.get("/findings/{finding_id}/retest-sessions", response_model=list[RetestSessionSummary])
    def list_finding_sessions(finding_id: int, session: SessionDep) -> list[RetestSessionSummary]:
        """List a finding's retest sessions, newest first (FR-17 6b-iii-b)."""
        rows = session.scalars(
            select(RetestSessionRecord)
            .where(RetestSessionRecord.finding_id == finding_id)
            .order_by(RetestSessionRecord.id.desc())
        )
        return [RetestSessionSummary.from_record(r) for r in rows]


def _register_report_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-01/FR-11 report-upload and status routes.

    Upload runs FR-01→FR-03 in a background task (:func:`run_extraction`); the
    two GET routes are the overview list and the poll target the SPA watches
    until the report settles on ``ready``/``failed``.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/reports", response_model=ReportOut, status_code=202)
    async def create_report(
        file: UploadFile,
        background: BackgroundTasks,
        session: SessionDep,
        agent: ExtractionAgentDep,
        metadata_agent: MetadataAgentDep,
        force: bool = False,
    ) -> ReportOut:
        """Accept a PDF report and schedule background extraction (FR-01/FR-03/FR-11).

        The bytes are SHA-256 hashed; if an identical report already exists and
        ``force`` is not set, responds ``409`` listing the duplicates so the
        operator can cancel or knowingly re-ingest (#134).
        """
        data = await file.read()
        if not data:
            raise HTTPException(status_code=422, detail="empty upload")
        content_hash = hashlib.sha256(data).hexdigest()
        _reject_if_duplicate(session, content_hash, force=force)
        report = ReportRecord(
            filename=file.filename or "report.pdf",
            status=ReportStatus.EXTRACTING.value,
            model=agent_model_name(agent),
            finding_count=0,
            content_hash=content_hash,
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        background.add_task(run_extraction, sessions, report.id, data, agent, metadata_agent)
        return ReportOut.from_record(report)

    @router.post("/reports/manual", response_model=ReportOut, status_code=201)
    def create_manual_report(payload: dict[str, Any], session: SessionDep) -> ReportOut:
        """Create a report and its findings directly, bypassing LLM extraction.

        The human-entry escape hatch (no FR-03): when a model cannot reliably
        ingest a report — e.g. a large report on a small local backend — a person
        supplies the findings by form or JSON upload. Reuses the FR-02 DefectDojo
        mapping per finding, then lands the report ``ready`` with its findings
        attached, so an agentic retest session (FR-17) starts identically to an
        extracted report's. ``payload`` is ``{"label": str, "findings": [...]}``.
        """
        try:
            findings = map_defectdojo_export(payload)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not findings:
            raise HTTPException(
                status_code=422, detail="a manual report needs at least one finding"
            )
        label = str(payload.get("label") or "").strip() or "Manual report"
        report = ReportRecord(
            filename=label,
            status=ReportStatus.READY.value,
            model="manual",
            finding_count=len(findings),
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        _persist_findings(session, findings, report_id=report.id)
        session.commit()
        session.refresh(report)
        return ReportOut.from_record(report)

    @router.get("/reports", response_model=list[ReportOut])
    def list_reports(session: SessionDep, archived: bool = False) -> list[ReportOut]:
        """List reports newest first, active by default (FR-11 overview).

        ``archived=false`` (default) is the overview; ``archived=true`` is the
        archived view. Archiving soft-hides a report without deleting it (#128).
        """
        records = session.scalars(
            select(ReportRecord)
            .where(ReportRecord.archived == archived)
            .order_by(ReportRecord.id.desc())
        )
        return [ReportOut.from_record(r) for r in records]

    @router.get("/reports/{report_id}", response_model=ReportOut)
    def get_report(report_id: int, session: SessionDep) -> ReportOut:
        """Return one report's current status (the SPA's poll target, FR-11)."""
        report = session.get(ReportRecord, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return ReportOut.from_record(report)


def _register_report_admin_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-11 report archive/delete routes (#128).

    Split from :func:`_register_report_routes` so each stays under the complexity
    gate; archiving soft-hides (reversible), deleting removes the report and all
    its derived rows.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.patch("/reports/{report_id}", response_model=ReportOut)
    def patch_report(report_id: int, body: ReportPatchIn, session: SessionDep) -> ReportOut:
        """Archive or unarchive a report — a reversible soft-hide (FR-11, #128)."""
        report = session.get(ReportRecord, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        report.archived = body.archived
        session.commit()
        session.refresh(report)
        return ReportOut.from_record(report)

    @router.delete("/reports/{report_id}", status_code=204)
    def delete_report(report_id: int, session: SessionDep) -> None:
        """Hard-delete a report and everything derived from it (FR-11, #128).

        Cascades by hand — SQLite enforces no FKs here — in dependency order:
        a report's findings own their version history, notes, verdicts and retest
        sessions, and each session owns its append-only transcript events. All of
        it is removed so no orphaned rows survive.
        """
        report = session.get(ReportRecord, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        _cascade_delete_report(session, report)

    @router.put("/reports/{report_id}/metadata", response_model=ReportOut)
    def put_report_metadata(report_id: int, body: ReportMetadata, session: SessionDep) -> ReportOut:
        """Replace a report's document metadata with the operator's edits (FR-03, #133)."""
        report = session.get(ReportRecord, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        report.doc_metadata = body.model_dump()
        session.commit()
        session.refresh(report)
        return ReportOut.from_record(report)


def _register_verdict_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the verdict-list + FR-10 audit routes (agentic-only after FR-17 6b-iii)."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/verdicts", response_model=list[VerdictOut])
    def list_verdicts(session: SessionDep) -> list[VerdictOut]:
        """List all persisted (agentic) verdicts (FR-09/FR-17)."""
        records = session.scalars(select(VerdictRecord).order_by(VerdictRecord.id))
        return [VerdictOut.from_record(r) for r in records]

    @router.get("/audit", response_model=AuditOut)
    def audit_rederive(session: SessionDep) -> AuditOut:
        """Re-derive every verdict from the transcript alone — no re-execution (FR-10)."""
        report = rederive_run(session)
        return AuditOut(
            total=report.total,
            reproduced=report.reproduced,
            ok=report.ok,
            discrepancies=[
                DiscrepancyOut(
                    verdict_id=d.verdict_id,
                    finding_id=d.finding_id,
                    stored=d.stored,
                    rederived=d.rederived,
                )
                for d in report.discrepancies
            ],
        )


# Poll interval for the FR-17 WS transcript tail (Task 7): frequent enough to feel
# live, coarse enough not to hammer SQLite on a single-user local install.
_WS_POLL_SECONDS = 0.25


def _load_stream_batch(
    session: Session, session_id: int, last_seq: int
) -> tuple[list[dict[str, Any]], bool] | None:
    """Return one WS poll tick's new events and terminal flag, or ``None`` if gone.

    Split out of :func:`_register_session_routes`'s WS handler to keep it under
    the mccabe complexity gate: the handler's ``while``/``for``/branches alone
    are enough to trip it once the DB read is inlined too.

    Args:
        session: Active DB session for the read.
        session_id: The retest session to read.
        last_seq: Only events with ``seq`` greater than this are returned.

    Returns:
        ``(new_events, is_terminal)``, or ``None`` if ``session_id`` doesn't exist.
    """
    record = session.get(RetestSessionRecord, session_id)
    if record is None:
        return None
    events = load_events_after(session, session_id, last_seq)
    return events, is_terminal(RetestSessionStatus(record.status))


def _register_session_routes(
    router: APIRouter, sessions: sessionmaker[Session], registry: SessionRegistry
) -> None:
    """Register the FR-17 agentic retest-session routes (start/poll/approve/reject/end).

    Split from :func:`_register_retest_routes` to keep each registration
    function's nested-route count under the mccabe gate (see
    :func:`_register_core_routes`). Starting a session and each command decision
    run in a FastAPI background task against a fresh session; the live in-memory
    orchestration state lives in the process-local ``registry`` (ADR-0025).
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post(
        "/findings/{finding_id}/retest-session", response_model=RetestSessionOut, status_code=202
    )
    def start_retest_session(
        finding_id: int,
        background: BackgroundTasks,
        session: SessionDep,
        agent: RetestAgentDep,
        make_sandbox: SandboxFactoryDep,
        goal_agent: GoalAgentDep,
        body: StartSessionRequest | None = None,
    ) -> RetestSessionOut:
        """Open an agentic retest session and schedule its first agent step (FR-17).

        An optional body sets the free-launch mode, a seed goal and the target
        scope (FR-17); with no body the session runs gated, requiring per-command
        operator approval.
        """
        cfg = body or StartSessionRequest()
        version = _current_or_404(session, finding_id)
        finding = version.to_domain()
        record = create_session(
            session,
            finding_id=finding_id,
            model=agent_model_name(agent),
            free_launch=cfg.free_launch,
        )
        background.add_task(
            run_first_step,
            sessions,
            registry,
            record.id,
            agent,
            make_sandbox,
            finding,
            goal_agent,
            tuple(cfg.initial_goal) if cfg.initial_goal else None,
            tuple(cfg.target_endpoints) if cfg.target_endpoints else None,
        )
        return RetestSessionOut.from_record(record, [])

    @router.get("/retest-sessions/{session_id}", response_model=RetestSessionOut)
    def get_retest_session(session_id: int, session: SessionDep) -> RetestSessionOut:
        """Return one session's status + full transcript (the SPA's poll target, FR-17)."""
        record = session.get(RetestSessionRecord, session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return RetestSessionOut.from_record(record, load_events_after(session, session_id, 0))

    @router.post("/retest-sessions/{session_id}/commands/{cid}/approve", status_code=202)
    def approve_command(session_id: int, cid: str, background: BackgroundTasks) -> dict[str, str]:
        """Approve the pending command; resume the run in the background (FR-17)."""
        background.add_task(run_decision, sessions, registry, session_id, True, "", cid)
        return {"status": "approved"}

    @router.post("/retest-sessions/{session_id}/commands/{cid}/reject", status_code=202)
    def reject_command(
        session_id: int,
        cid: str,
        background: BackgroundTasks,
        body: RejectRequest | None = None,
    ) -> dict[str, str]:
        """Reject the pending command with an optional reason; resume the run (FR-17)."""
        reason = body.reason if body else ""
        background.add_task(run_decision, sessions, registry, session_id, False, reason, cid)
        return {"status": "rejected"}

    @router.post("/retest-sessions/{session_id}/human-command", status_code=202)
    def human_command(
        session_id: int, body: HumanCommandRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Run a manual operator command (`!`) in the session's sandbox (FR-17 Slice 2).

        Ungated (single trusted user, ADR-0008); runs in the background and
        surfaces via the transcript stream, then the agent observes it on its
        next turn. A no-op if the session is no longer live.
        """
        background.add_task(run_human_command, sessions, registry, session_id, body.command)
        return {"status": "accepted"}

    @router.post("/retest-sessions/{session_id}/message", status_code=202)
    def send_message(
        session_id: int,
        body: MessageRequest,
        background: BackgroundTasks,
        session: SessionDep,
        qa_agent: QaAgentDep,
    ) -> dict[str, str]:
        """Queue an operator chat message and trigger an immediate agent reply (FR-17).

        The message is recorded and buffered for the agent's next turn (steering) and,
        additively, answered right away by a read-only Q&A over the transcript — so a
        question ("what are we retesting?") gets a reply without an approve/reject. A
        no-op if the session is no longer live.
        """
        background.add_task(
            run_message,
            sessions,
            registry,
            session_id,
            body.text,
            qa_agent,
            _session_finding(session, session_id),
        )
        return {"status": "accepted"}

    @router.post("/retest-sessions/{session_id}/end", status_code=202)
    def end_retest_session(session_id: int, session: SessionDep) -> dict[str, str]:
        """Operator-initiated end: tear down and mark the session ended (FR-17)."""
        end_session(session, registry, session_id)
        return {"status": "ended"}


def _register_free_launch_route(
    router: APIRouter, sessions: sessionmaker[Session], registry: SessionRegistry
) -> None:
    """Register the FR-17 Slice 5 free-launch toggle route.

    Split from :func:`_register_session_routes` to keep each registration
    function's nested-route count under the mccabe gate (see
    :func:`_register_core_routes`): adding an eighth route to the shared
    registrar would trip it.
    """

    @router.post("/retest-sessions/{session_id}/free-launch", status_code=202)
    def set_free_launch_route(
        session_id: int, body: FreeLaunchRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Toggle free-launch mode on a live session (FR-17 Slice 5).

        Enabling auto-approves a pending command and lets the agent's commands
        auto-run (plan changes stay gated); disabling re-arms the per-command
        gate. Runs in the background; a no-op if the session is no longer live.
        """
        background.add_task(run_free_launch, sessions, registry, session_id, body.enabled)
        return {"status": "accepted"}


def _register_guidance_routes(
    router: APIRouter, sessions: sessionmaker[Session], registry: SessionRegistry
) -> None:
    """Register the ADR-0034 pause-and-ask routes (keep going + manual conclude).

    Split from :func:`_register_session_routes` for the same mccabe-budget reason
    as :func:`_register_free_launch_route`.
    """

    @router.post("/retest-sessions/{session_id}/continue", status_code=202)
    def continue_route(session_id: int, background: BackgroundTasks) -> dict[str, str]:
        """Keep going on a paused session (ADR-0034 "Keep going").

        Resumes the agent — a held command re-opens its gate, an exhausted-options
        pause re-runs the agent with any queued guidance. Runs in the background; a
        no-op unless the session is paused with a live agent.
        """
        background.add_task(run_continue, sessions, registry, session_id)
        return {"status": "accepted"}

    @router.post("/retest-sessions/{session_id}/conclude", status_code=202)
    def conclude_route(
        session_id: int, body: ConcludeRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Manually conclude a session with the operator's determination (ADR-0034).

        Records the operator's verdict (the only path that writes ``inconclusive``)
        and tears down. Runs in the background; a no-op if the session is terminal.
        """
        background.add_task(
            run_conclude, sessions, registry, session_id, body.status, body.rationale
        )
        return {"status": "accepted"}


def _register_goal_routes(
    router: APIRouter, sessions: sessionmaker[Session], registry: SessionRegistry
) -> None:
    """Register the FR-17 6b-ii user-owned goal routes (edit + regenerate).

    Split from :func:`_register_session_routes` for the same mccabe-budget reason
    as :func:`_register_free_launch_route`.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/retest-sessions/{session_id}/goal", status_code=202)
    def set_goal_route(
        session_id: int, body: GoalRequest, background: BackgroundTasks
    ) -> dict[str, str]:
        """Set the user-owned goal on a live session (FR-17 6b-ii).

        Updates the "Current goal" panel and is delivered to the agent on its next
        turn (pure-queue). Runs in the background; a no-op if the session is no
        longer live.
        """
        background.add_task(run_goal, sessions, registry, session_id, body.steps)
        return {"status": "accepted"}

    @router.post("/retest-sessions/{session_id}/goal/regenerate", status_code=202)
    def regenerate_goal_route(
        session_id: int,
        session: SessionDep,
        goal_agent: GoalAgentDep,
        background: BackgroundTasks,
    ) -> dict[str, str]:
        """Regenerate the goal for a session's finding and set it (FR-17 6b-ii)."""
        record = session.get(RetestSessionRecord, session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown session")
        finding = _current_or_404(session, record.finding_id).to_domain()
        background.add_task(
            run_regenerate_goal, sessions, registry, session_id, goal_agent, finding
        )
        return {"status": "accepted"}


def _register_adjudicate_route(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-17 Slice 6a verdict-adjudication route.

    Split from :func:`_register_session_routes` for the same mccabe-budget reason
    as :func:`_register_free_launch_route`. Runs **inline** (not a background
    task): adjudication is a quick, registry-free DB write, and running it inline
    means the superseding verdict + transcript event exist by the time the
    response returns, so a follow-up ``GET /verdicts`` or ``/export`` sees them.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.post("/retest-sessions/{session_id}/adjudicate", status_code=200)
    def adjudicate(session_id: int, body: AdjudicateRequest, session: SessionDep) -> dict[str, str]:
        """Accept or override a concluded session's agent verdict (FR-17 Slice 6a).

        Appends a superseding operator verdict + a ``verdict_adjudicated``
        transcript event (append-only; the agent's record is untouched). A no-op
        if the session doesn't exist or has no agent verdict yet.
        """
        adjudicate_verdict(session, session_id, body.status, body.rationale)
        return {"status": "adjudicated"}


def _register_session_stream_route(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-17 WS transcript-tail route.

    Split from :func:`_register_session_routes` to keep each registration
    function's nested-route count under the mccabe gate (see
    :func:`_register_core_routes`): the WS handler's own ``while``/``for``/
    ``try`` branches are enough on their own to trip the shared registrar's
    budget once added on top of its five REST routes.
    """

    @router.websocket("/retest-sessions/{session_id}/stream")
    async def stream_session(websocket: WebSocket, session_id: int) -> None:
        """Tail one retest session's transcript over a WebSocket (FR-17).

        On connect, replays every persisted event in ``seq`` order, then polls
        every ``_WS_POLL_SECONDS`` for newly appended ones — each sent as
        ``{seq, kind, payload}`` JSON — until the session reaches a terminal
        status with nothing left to send, at which point the server closes
        the socket on its own. Closes immediately with code 1008 (policy
        violation) if ``session_id`` doesn't exist.

        Args:
            websocket: The accepted client connection.
            session_id: The retest session to tail.
        """
        await websocket.accept()
        last_seq = 0
        try:
            while True:
                with sessions() as session:
                    batch = _load_stream_batch(session, session_id, last_seq)
                if batch is None:
                    await websocket.close(code=1008)
                    return
                events, terminal = batch
                for event in events:
                    await websocket.send_json(event)
                    last_seq = event["seq"]
                if terminal and not events:
                    await websocket.close()
                    return
                await asyncio.sleep(_WS_POLL_SECONDS)
        except WebSocketDisconnect:
            return


def _register_export_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the FR-12 versioned run-export routes.

    ``/export`` serves the whole run as one versioned JSON document (the FR-15
    evaluation harness consumes it); ``/export/schema`` serves the JSON Schema
    that document validates against.
    """

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/export", response_model=RunExport)
    def export_run(session: SessionDep) -> RunExport:
        """Export the complete run as a versioned JSON document (FR-12)."""
        return build_export(session)

    @router.get("/export/schema")
    def export_run_schema() -> dict[str, Any]:
        """Return the published JSON Schema the export validates against (FR-12)."""
        return export_schema()


def _register_settings_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the ADR-0021 model/provider settings routes."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/settings", response_model=SettingsOut)
    def read_settings(session: SessionDep) -> SettingsOut:
        """Return the current model/provider setting (key masked)."""
        return SettingsOut.from_domain(load_or_seed(session))

    @router.put("/settings", response_model=SettingsOut)
    def update_settings(body: SettingsUpdateIn, session: SessionDep) -> SettingsOut:
        """Persist a new model/provider setting; takes effect on the next agent build."""
        cfg = save(
            session,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
            clear_key=body.clear_key,
        )
        return SettingsOut.from_domain(cfg)

    @router.post("/settings/probe", response_model=ProbeResult)
    def probe_settings(body: ProbeIn) -> ProbeResult:
        """Discover models / test reachability for a provider base URL (ADR-0021)."""
        return probe_provider(body.base_url, body.api_key)

    @router.get("/settings/status", response_model=BackendStatusOut)
    def backend_status(session: SessionDep) -> BackendStatusOut:
        """Report whether the configured LLM backend is reachable + the active model.

        Probes the stored provider config (so keyed backends work without the UI
        re-sending the key) and feeds the sidebar's connection pill. A native
        provider with no base URL can't be cheaply probed, so it reports its
        model as connected rather than falsely red.
        """
        cfg = load_or_seed(session)
        connected = (
            True if not cfg.base_url else probe_provider(cfg.base_url, cfg.api_key).reachable
        )
        return BackendStatusOut(connected=connected, model=cfg.model)


def _mount_spa(app: FastAPI, dist: Path = _SPA_DIST) -> None:
    """Serve the built SPA at ``/`` with a client-routing catch-all (FR-11).

    No-op when the build is absent (backend-only dev, CI unit runs) — the API
    still works. Registered after the ``/api`` router so it never shadows it;
    the catch-all serves any real built file and otherwise returns
    ``index.html`` so deep links (``/findings/5``) reload into the SPA. Binds
    nothing new: the app still listens only on ``127.0.0.1`` (NFR-03).
    """
    index = dist / "index.html"
    if not index.is_file():
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api"):
            raise HTTPException(status_code=404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def _reject_if_duplicate(session: Session, content_hash: str, *, force: bool) -> None:
    """Refuse a re-upload of already-ingested bytes unless the operator forces it (#134).

    Raises ``409`` listing the matching reports (id/filename/created_at) so the UI
    can offer cancel-or-continue; a truthy ``force`` skips the check for a knowing
    re-ingest. Extracted as a helper so the upload route stays under the gate.
    """
    if force:
        return
    dupes = session.scalars(
        select(ReportRecord)
        .where(ReportRecord.content_hash == content_hash)
        .order_by(ReportRecord.id.desc())
    ).all()
    if not dupes:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "This report has already been uploaded.",
            "duplicates": [
                {"id": d.id, "filename": d.filename, "created_at": d.created_at.isoformat()}
                for d in dupes
            ],
        },
    )


def _cascade_delete_report(session: Session, report: ReportRecord) -> None:
    """Delete a report and every row derived from it, in dependency order (#128).

    SQLite enforces no foreign keys here, so the cascade is explicit: a report's
    findings own their version history, notes, verdicts and retest sessions, and
    each session owns its append-only transcript events — all removed so no
    orphaned rows survive. The caller owns the surrounding request.
    """
    finding_ids = list(
        session.scalars(select(FindingRecord.id).where(FindingRecord.report_id == report.id))
    )
    if finding_ids:
        session_ids = list(
            session.scalars(
                select(RetestSessionRecord.id).where(
                    RetestSessionRecord.finding_id.in_(finding_ids)
                )
            )
        )
        if session_ids:
            session.execute(
                delete(SessionEventRecord).where(SessionEventRecord.session_id.in_(session_ids))
            )
        session.execute(delete(VerdictRecord).where(VerdictRecord.finding_id.in_(finding_ids)))
        session.execute(
            delete(RetestSessionRecord).where(RetestSessionRecord.finding_id.in_(finding_ids))
        )
        session.execute(
            delete(FindingNoteRecord).where(FindingNoteRecord.finding_id.in_(finding_ids))
        )
        session.execute(
            delete(FindingVersionRecord).where(FindingVersionRecord.finding_id.in_(finding_ids))
        )
        session.execute(delete(FindingRecord).where(FindingRecord.id.in_(finding_ids)))
    session.delete(report)
    session.commit()


def _fail_orphaned_extractions(sessions: sessionmaker[Session]) -> None:
    """Settle reports left mid-extraction by a prior crash or restart (FR-01).

    Extraction runs as an in-memory background task, so a process restart
    orphans any report still in ``extracting``: nothing would ever move it to a
    terminal state, and the UI's status poll would spin forever. Sweeping these
    rows to ``failed`` at startup guarantees a report never stays stuck in
    ``extracting`` (issue #131) — the operator can then re-upload it.

    Args:
        sessions: The app's session factory.
    """
    with sessions() as session:
        orphaned = session.scalars(
            select(ReportRecord).where(ReportRecord.status == ReportStatus.EXTRACTING.value)
        ).all()
        if not orphaned:
            return
        for report in orphaned:
            report.status = ReportStatus.FAILED.value
            report.error = "extraction interrupted by a restart — please re-upload the report"
        session.commit()


def create_app(db_path: str = "revalid.db", engine: Engine | None = None) -> FastAPI:
    """Build the application with its own database engine.

    Args:
        db_path: SQLite file backing this instance; ignored when ``engine``
            is given (tests inject an in-memory engine).
        engine: Pre-built engine to use instead of opening ``db_path``.

    Returns:
        The configured FastAPI application.
    """
    db_engine = engine if engine is not None else create_db_engine(db_path)
    sessions = session_factory(db_engine)
    _fail_orphaned_extractions(sessions)
    app = FastAPI(title="revalid", version=__version__)
    app.state.sessions = sessions
    registry = SessionRegistry()
    app.state.registry = registry

    api = APIRouter(prefix="/api")
    _register_core_routes(api, sessions)
    _register_finding_routes(api, sessions)
    _register_finding_retest_routes(api, sessions)
    _register_report_routes(api, sessions)
    _register_report_admin_routes(api, sessions)
    _register_verdict_routes(api, sessions)
    _register_session_routes(api, sessions, registry)
    _register_free_launch_route(api, sessions, registry)
    _register_guidance_routes(api, sessions, registry)
    _register_goal_routes(api, sessions, registry)
    _register_adjudicate_route(api, sessions)
    _register_session_stream_route(api, sessions)
    _register_export_routes(api, sessions)
    _register_settings_routes(api, sessions)
    app.include_router(api)
    _mount_spa(app)

    return app
