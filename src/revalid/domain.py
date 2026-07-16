"""Domain schemas shared across ingestion, planning, and execution (ADR-0002).

These Pydantic models are the internal representation every layer speaks.
``Probe`` and ``Verdict`` join this module with FR-07/FR-09.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(enum.StrEnum):
    """Normalized severity scale for findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Settings(BaseModel):
    """User-configurable LLM backend selection (FR-13 / ADR-0021).

    Attributes:
        model: A Pydantic AI ``provider:model`` string (e.g. ``ollama:qwen3.6:27b``).
        base_url: Provider base URL for OpenAI-compatible backends (Ollama and
            friends); ``None`` for native providers configured from the environment.
        api_key: Provider API key, or ``None`` when supplied via the environment.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None


class Finding(BaseModel):
    """A single pentest finding in the internal model (FR-02/FR-03).

    Attributes:
        title: Short human-readable name of the finding.
        severity: Normalized severity level.
        description: Free-text description of the vulnerability.
        impact: What an attacker gains / the business consequence (FR-03).
        attack_vector: How the vulnerability is reached and exploited (FR-03).
        affected_endpoints: URLs or endpoint identifiers the finding applies to.
        reproduction_steps: Ordered steps to reproduce the issue.
        raw: Complete source payload as ingested, preserving fields the
            internal model does not map (FR-02 audit criterion). For
            LLM-extracted findings it also carries extraction lineage — model
            name and source text — for the audit trail (FR-10 / NFR-02).
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    severity: Severity
    description: str = ""
    impact: str = ""
    attack_vector: str = ""
    affected_endpoints: tuple[str, ...] = ()
    reproduction_steps: tuple[str, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)


class FindingOrigin(enum.StrEnum):
    """How a finding version came to be (FR-16, ADR-0024).

    Extraction is version 1 — the finding as the LLM/import first produced it;
    every later version is an operator ``EDIT``. The distinction is audit
    lineage: which content the machine proposed vs. what a human corrected.
    """

    EXTRACTION = "extraction"
    EDIT = "edit"


class FindingStage(enum.StrEnum):
    """The pipeline stage a note was written on (FR-16, ADR-0024).

    The five stages mirror the finding's lifecycle track
    (extract → plan → approve → retest → verdict); ``GENERAL`` tags a note left
    from the finding overview rather than a specific stage.
    """

    EXTRACT = "extract"
    PLAN = "plan"
    APPROVE = "approve"
    RETEST = "retest"
    VERDICT = "verdict"
    GENERAL = "general"


class Probe(BaseModel):
    """A single verification-only HTTP action used to retest a finding (FR-07).

    Probes are non-destructive: they observe whether a vulnerability is still
    present, never exploit it for impact. In the walking skeleton exactly one
    ``kind`` exists; more join as their finding types land.

    Attributes:
        kind: Stable identifier of the probe type (e.g. ``sqli-login-bypass``).
        method: HTTP method.
        url: Absolute target URL; always checked against the allowlist (FR-06)
            before any socket opens.
        headers: Request headers to send.
        json_body: JSON request body, or ``None`` for a body-less request.
        expected_indicator: Human-readable note on what a still-open result
            looks like — documentation, not matching logic.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    expected_indicator: str = ""


class RetestPlan(BaseModel):
    """An ordered, approved-once set of typed actions to retest a finding (FR-04).

    A plan is derived from a finding's reproduction steps and holds only
    :class:`Probe` actions whose targets are on the FR-06 allowlist — the
    generator drops anything else before it lands here, so a plan references
    only authorized targets by construction. Plans are inert until approved
    (FR-05); ``version`` is ``1`` at generation and bumps when FR-05 edits it.

    Attributes:
        finding_title: Title of the finding this plan retests (the link to it).
        actions: The ordered typed probe actions to run.
        version: Plan revision, starting at 1 (FR-05 versions edited plans).
        raw: Generation lineage — model name and source finding — for the
            audit trail (FR-10 / NFR-02).
    """

    model_config = ConfigDict(frozen=True)

    finding_title: str = Field(min_length=1)
    actions: tuple[Probe, ...] = ()
    version: int = Field(default=1, ge=1)
    raw: dict[str, Any] = Field(default_factory=dict)


class PlanStatus(enum.StrEnum):
    """Lifecycle state of a persisted retest-plan version (FR-05).

    A version is born ``GENERATING`` when FR-04 generation is scheduled in the
    background (ADR-0022) and always settles: to ``PROPOSED`` once its gated
    actions are persisted, or ``FAILED`` (with the error recorded) if the model
    produced nothing runnable — so the UI's plan poll always terminates, exactly
    as an uploaded report's does. A user edit inserts a ``PROPOSED`` version
    directly (no generation), and the approve/reject decision moves it on.
    """

    GENERATING = "generating"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class ReportStatus(enum.StrEnum):
    """Lifecycle state of an uploaded report's ingest job (FR-01/FR-11).

    A report starts ``EXTRACTING`` when uploaded and always settles on exactly
    one terminal state — ``READY`` once its findings are persisted, or
    ``FAILED`` (with the error recorded) if extraction never completes — so the
    UI's status poll is guaranteed to terminate.
    """

    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"


class RetestSessionStatus(enum.StrEnum):
    """Lifecycle of an FR-17 agentic retest session.

    A session always reaches a terminal state (``CONCLUDED``/``GIVEN_UP``/
    ``ENDED``/``ERROR``) so the SPA poll and the WS tail terminate.
    """

    STARTING = "starting"
    AWAITING_COMMAND = "awaiting_command"
    RUNNING_COMMAND = "running_command"
    CONCLUDED = "concluded"
    GIVEN_UP = "given_up"
    ENDED = "ended"
    ERROR = "error"


class SessionEventKind(enum.StrEnum):
    """Kinds of append-only transcript event (FR-17 audit trail)."""

    AGENT_MESSAGE = "agent_message"
    COMMAND_PROPOSED = "command_proposed"
    COMMAND_APPROVED = "command_approved"
    COMMAND_REJECTED = "command_rejected"
    COMMAND_OUTPUT = "command_output"
    STATE_CHANGE = "state_change"
    VERDICT = "verdict"
    ERROR = "error"


class Evidence(BaseModel):
    """Captured request/response of one executed probe step (FR-07).

    Every field a verdict is justified by is recorded here so the verdict can
    be re-derived and audited. ``response_status`` is ``0`` when the target was
    unreachable and no HTTP response was received.

    Attributes:
        request_method: Method actually sent.
        request_url: URL actually requested.
        request_body: Serialized request body (empty string if none).
        response_status: HTTP status code, or ``0`` if no response arrived.
        response_headers: Response headers as received.
        response_body_excerpt: Leading slice of the response body.
        elapsed_ms: Wall-clock round-trip time in milliseconds.
    """

    model_config = ConfigDict(frozen=True)

    request_method: str
    request_url: str
    request_body: str = ""
    response_status: int
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body_excerpt: str = ""
    elapsed_ms: float = 0.0


class VerdictStatus(enum.StrEnum):
    """Outcome of retesting a finding (FR-09)."""

    STILL_OPEN = "still_open"
    FIXED = "fixed"
    INCONCLUSIVE = "inconclusive"


class Verdict(BaseModel):
    """A retest outcome for a finding, bound to the evidence that justifies it.

    Enforces the FR-09 invariants at the type level: a verdict cannot be built
    without ``evidence`` (no verdict without linked evidence) and always carries
    a machine-readable ``reason_code`` (required for inconclusive results).

    Attributes:
        status: still-open / fixed / inconclusive.
        reason_code: Machine-readable justification token
            (e.g. ``sqli_auth_bypass_succeeded``, ``endpoint_changed``).
        rationale: Human-readable explanation of the verdict.
        matched_indicators: Indicator tokens observed in the evidence.
        evidence: The request/response the verdict is derived from.
    """

    model_config = ConfigDict(frozen=True)

    status: VerdictStatus
    reason_code: str = Field(min_length=1)
    rationale: str = ""
    matched_indicators: tuple[str, ...] = ()
    evidence: Evidence
