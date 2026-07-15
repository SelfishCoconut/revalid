"""Unit tests for the FR-12 versioned run export.

Covers the assembled document shape, the run metrics, the published-schema drift
guard, and genuine JSON Schema validation of a built export (the FR-12 AC).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from jsonschema import validate
from jsonschema.protocols import Validator
from sqlalchemy.orm import Session

from revalid.approval import approve_plan, execute_approved_plan, save_generated_plan
from revalid.db import IN_MEMORY, FindingRecord, create_db_engine, session_factory
from revalid.domain import Finding, Probe, RetestPlan, Severity
from revalid.export import SCHEMA_VERSION, build_export, export_schema
from revalid.plan import PlanResult

_PUBLISHED_SCHEMA = (
    Path(__file__).resolve().parents[2] / "docs/reference/schemas/run-export.schema.json"
)


def _plan() -> PlanResult:
    probe = Probe(
        kind="sqli-login-bypass",
        method="POST",
        url="http://localhost:3000/rest/user/login",
        json_body={"email": "' OR 1=1--", "password": "x"},
    )
    plan = RetestPlan(
        finding_title="SQLi login", actions=(probe,), raw={"finding_title": "SQLi login"}
    )
    return PlanResult(plan=plan)


def _session_with_verdict() -> Session:
    """Build an in-memory run: finding -> approved plan -> one stored verdict."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(FindingRecord.from_domain(Finding(title="SQLi login", severity=Severity.CRITICAL)))
    session.commit()
    save_generated_plan(session, 1, _plan())
    approve_plan(session, 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        execute_approved_plan(session, client, 1)
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
    assert len(export.plans) == 1 and export.plans[0].status == "approved"
    assert len(export.verdicts) == 1
    verdict = export.verdicts[0]
    assert verdict.finding_id == 1
    assert verdict.actor == "executor"
    assert verdict.verdict.status.value == "still_open"
    assert verdict.plan_version == 1

    assert export.metrics.findings == 1
    assert export.metrics.plans == 1
    assert export.metrics.verdicts == 1
    assert export.metrics.verdicts_by_status["still_open"] == 1
    assert export.metrics.total_elapsed_ms == verdict.verdict.evidence.elapsed_ms
    assert export.metrics.mean_elapsed_ms == export.metrics.total_elapsed_ms


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
