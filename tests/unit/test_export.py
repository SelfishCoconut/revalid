"""Unit tests for the FR-12 versioned run export.

Covers the assembled document shape, the run metrics, the published-schema drift
guard, and genuine JSON Schema validation of a built export (the FR-12 AC).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import validate
from jsonschema.protocols import Validator
from sqlalchemy.orm import Session

from revalid import retest_session as rs
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import (
    AgenticEvidence,
    Finding,
    FindingStage,
    SessionEventKind,
    Severity,
    VerdictStatus,
)
from revalid.export import SCHEMA_VERSION, build_export, export_schema
from revalid.findings import add_note, add_version, create_finding

_PUBLISHED_SCHEMA = (
    Path(__file__).resolve().parents[2] / "docs/reference/schemas/run-export.schema.json"
)


def _session_with_verdict() -> Session:
    """Build an in-memory run: finding -> agentic session -> one concluded verdict."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    create_finding(session, Finding(title="SQLi login", severity=Severity.CRITICAL))
    session.commit()
    sid = rs.create_session(session, finding_id=1, model="m").id
    rs.append_event(
        session,
        sid,
        SessionEventKind.COMMAND_OUTPUT,
        {
            "command": "curl -s http://lab/login",
            "stdout": "{token}",
            "stderr": "",
            "exit_code": 0,
            "elapsed_ms": 12,
        },
    )
    rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
    return session


def test_published_schema_matches_model() -> None:
    """The committed schema must equal the model's — regenerate via `make export-schema`."""
    published = json.loads(_PUBLISHED_SCHEMA.read_text())
    assert published == export_schema(), "run `make export-schema` to refresh the published schema"


def test_published_schema_is_itself_valid() -> None:
    """The published schema is a well-formed JSON Schema."""
    Validator.check_schema(export_schema())


def test_export_of_empty_db_is_schema_valid() -> None:
    session = session_factory(create_db_engine(IN_MEMORY))()
    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))
    assert export.schema_version == SCHEMA_VERSION
    assert export.metrics.verdicts == 0
    assert export.metrics.mean_elapsed_ms == 0.0
    # Every VerdictStatus key is present even with no verdicts (stable shape).
    assert set(export.metrics.verdicts_by_status) == {"still_open", "fixed", "inconclusive"}
    validate(instance=export.model_dump(mode="json"), schema=export_schema())


def test_export_document_validates_against_published_schema() -> None:
    """FR-12 AC: a real export validates against the published JSON schema."""
    session = _session_with_verdict()
    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))
    validate(
        instance=export.model_dump(mode="json"),
        schema=json.loads(_PUBLISHED_SCHEMA.read_text()),
    )


def test_export_assembles_run_and_metrics() -> None:
    session = _session_with_verdict()
    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))

    assert export.generator.tool == "revalid"
    assert len(export.findings) == 1
    assert export.findings[0].finding.title == "SQLi login"
    assert len(export.verdicts) == 1
    verdict = export.verdicts[0]
    assert verdict.finding_id == 1
    assert verdict.actor == "agent"
    assert verdict.status.value == "still_open"
    assert verdict.evidence is not None

    assert export.metrics.findings == 1
    assert export.metrics.verdicts == 1
    assert export.metrics.verdicts_by_status["still_open"] == 1
    assert export.metrics.total_elapsed_ms == verdict.evidence.elapsed_ms
    assert export.metrics.mean_elapsed_ms == export.metrics.total_elapsed_ms


def test_export_carries_agentic_verdict_evidence() -> None:
    """An agentic verdict exports its flexible command-output evidence (FR-17 6b-i)."""
    session = _session_with_verdict()
    export = build_export(session, generated_at=datetime(2026, 7, 18, tzinfo=UTC))
    [verdict] = export.verdicts
    assert verdict.session_id is not None
    assert verdict.actor == "agent"
    assert isinstance(verdict.evidence, AgenticEvidence)
    assert verdict.evidence.explanation == "auth still bypassable"
    assert verdict.evidence.command == "curl -s http://lab/login"
    # The captured command's timing counts in the run metrics.
    assert export.metrics.total_elapsed_ms == 12.0
    validate(instance=export.model_dump(mode="json"), schema=export_schema())


def test_export_carries_finding_versions_and_notes() -> None:
    """FR-16: the export carries each finding's version history and stage-tagged notes."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    record = create_finding(session, Finding(title="SQLi login", severity=Severity.HIGH))
    session.commit()
    add_version(
        session,
        record.id,
        Finding(title="SQLi login", severity=Severity.CRITICAL, description="edited"),
        edited_by="user",
        reason="raise severity",
    )
    add_note(session, record.id, FindingStage.GOAL, "check /admin too")

    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))
    finding = export.findings[0]
    assert finding.version == 2
    # ``finding`` is the current version's content.
    assert finding.finding.severity.value == "critical"
    assert [v.version for v in finding.versions] == [1, 2]
    assert finding.versions[0].origin == "extraction"
    assert finding.versions[1].origin == "edit"
    assert finding.versions[1].reason == "raise severity"
    assert [n.body for n in finding.notes] == ["check /admin too"]
    assert finding.notes[0].stage == "goal"
    validate(instance=export.model_dump(mode="json"), schema=export_schema())


def test_export_includes_reports() -> None:
    """A report row is exported and counted in the metrics (FR-01 lineage)."""
    from revalid.db import ReportRecord

    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(
        ReportRecord(filename="scan.pdf", status="ready", model="ollama:x", finding_count=3)
    )
    session.commit()
    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))
    assert export.metrics.reports == 1
    assert export.reports[0].filename == "scan.pdf"
    assert export.reports[0].model == "ollama:x"
    validate(instance=export.model_dump(mode="json"), schema=export_schema())


def test_generated_at_defaults_to_now_when_omitted() -> None:
    session = session_factory(create_db_engine(IN_MEMORY))()
    before = datetime.now(UTC)
    export = build_export(session)
    assert export.generated_at >= before


def test_export_endpoint_returns_schema_valid_document() -> None:
    """FR-12 over HTTP: GET /api/export returns a document valid against /api/export/schema."""
    from revalid.app import create_app

    with TestClient(create_app(engine=create_db_engine(IN_MEMORY))) as client:
        schema = client.get("/api/export/schema").json()
        document = client.get("/api/export").json()
    assert document["schema_version"] == SCHEMA_VERSION
    validate(instance=document, schema=schema)
