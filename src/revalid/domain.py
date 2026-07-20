"""Domain schemas shared across ingestion, goal-setting, and agentic retest (ADR-0002).

These Pydantic models are the internal representation every layer speaks.
``AgenticEvidence`` and ``VerdictStatus`` join this module with the FR-17 retest.
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


class CvssCode(BaseModel):
    """CVSS severity code attached to a finding at ingestion (FR-19).

    ``vector`` is the CVSS base vector string (e.g.
    ``CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H``); ``base_score`` is the
    derived 0.0--10.0 base score. ``inferred`` records provenance: ``False``
    when the code was read from the report verbatim, ``True`` when the model
    derived it because the report stated none. An empty ``vector`` with
    ``inferred=False`` means the report had no CVSS code and none was derived.
    """

    model_config = ConfigDict(frozen=True)

    vector: str = ""
    base_score: float | None = None
    inferred: bool = False


class MitreMapping(BaseModel):
    """MITRE ATT&CK technique mapping for a finding (FR-19).

    ``techniques`` are ATT&CK technique IDs (e.g. ``T1190``,
    ``T1110``) the finding maps onto; ``inferred`` is ``True`` when the model
    derived the mapping rather than reading it from the report. Empty
    ``techniques`` with ``inferred=False`` means none stated and none derived.
    """

    model_config = ConfigDict(frozen=True)

    techniques: tuple[str, ...] = ()
    inferred: bool = False


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
        cvss: CVSS severity code, read from the report or derived at ingestion
            (FR-19). Provenance is on the ``inferred`` flag.
        mitre: MITRE ATT&CK technique mapping, read or derived at ingestion
            (FR-19). Provenance is on the ``inferred`` flag.
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
    cvss: CvssCode = Field(default_factory=CvssCode)
    mitre: MitreMapping = Field(default_factory=MitreMapping)
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

    The stages mirror the finding's lifecycle track
    (extract → goal → retest → verdict); the ``PLAN`` and ``APPROVE`` values are
    retained for backward compatibility (the goal stage tags its notes ``plan``),
    while ``GENERAL`` tags a note left from the finding overview rather than a
    specific stage.
    """

    EXTRACT = "extract"
    PLAN = "plan"
    APPROVE = "approve"
    RETEST = "retest"
    VERDICT = "verdict"
    GENERAL = "general"


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

    A session reaches a terminal state (``CONCLUDED``/``ENDED``/``ERROR``) only
    on a real determination or an operator action; between turns it may pause in
    the non-terminal ``NEEDS_GUIDANCE`` state, asking the operator to steer or
    conclude (ADR-0034). ``GIVEN_UP`` is retired — kept only so any legacy row
    stays terminal.
    """

    STARTING = "starting"
    #: The agent is computing its next turn (an LLM call is in flight). Emitted
    #: before each ``agent.run_sync`` so the console can show a live "thinking"
    #: indicator while local models — which can take a while — work (FR-17).
    THINKING = "thinking"
    AWAITING_COMMAND = "awaiting_command"
    RUNNING_COMMAND = "running_command"
    #: Paused mid-session: the agent exhausted the options it could think of and
    #: handed back to the operator (ADR-0034). Non-terminal — the sandbox stays
    #: alive; the operator steers and keeps going, or concludes.
    NEEDS_GUIDANCE = "needs_guidance"
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
    HUMAN_COMMAND = "human_command"
    HUMAN_MESSAGE = "human_message"
    # The current guiding goal (FR-17 6b-ii: user-owned; formerly the agent's set_plan).
    PLAN_UPDATED = "plan_updated"
    #: The retest scope set at launch (FR-17): payload ``endpoints`` is the exact
    #: list of target URLs the agent must confine itself to. Emitted once at session
    #: start and never again — reachability is fixed when the sandbox is provisioned,
    #: so changing scope needs a fresh session (Restart), not a live edit.
    TARGET_SET = "target_set"
    #: The session paused for operator guidance (ADR-0034); payload carries the
    #: human-readable ``reason`` the agent gave when it handed back.
    NEEDS_GUIDANCE = "needs_guidance"
    STATE_CHANGE = "state_change"
    FREE_LAUNCH_CHANGED = "free_launch_changed"
    VERDICT = "verdict"
    VERDICT_ADJUDICATED = "verdict_adjudicated"
    ERROR = "error"


class AgenticEvidence(BaseModel):
    """Flexible proof backing an agentic verdict (FR-17 Slice 6b) — tool-agnostic.

    An agentic retest runs arbitrary tooling (not just HTTP probes), so its
    evidence is the agent's explanation plus the decisive command's real output,
    not a structured request/response. The orchestrator captures it on conclude
    from the transcript's last ``command_output`` (real data, not the model
    restating it); ``command``/``output`` are empty when the agent concluded
    without running a command.

    Attributes:
        explanation: The agent's account of what proves the verdict (its rationale).
        command: The decisive command the agent ran.
        output: That command's captured stdout/stderr excerpt (truncated).
        exit_code: The command's exit status, or ``None`` when no command ran.
        elapsed_ms: The command's wall-clock time in milliseconds.
    """

    model_config = ConfigDict(frozen=True)

    explanation: str
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    elapsed_ms: float = 0.0


class VerdictStatus(enum.StrEnum):
    """Outcome of retesting a finding (FR-09)."""

    STILL_OPEN = "still_open"
    FIXED = "fixed"
    INCONCLUSIVE = "inconclusive"
