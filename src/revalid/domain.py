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


class Finding(BaseModel):
    """A single pentest finding in the internal model (FR-02/FR-03).

    Attributes:
        title: Short human-readable name of the finding.
        severity: Normalized severity level.
        description: Free-text description of the vulnerability.
        affected_endpoints: URLs or endpoint identifiers the finding applies to.
        reproduction_steps: Ordered steps to reproduce the issue.
        raw: Complete source payload as ingested, preserving fields the
            internal model does not map (FR-02 audit criterion).
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    severity: Severity
    description: str = ""
    affected_endpoints: tuple[str, ...] = ()
    reproduction_steps: tuple[str, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)
