"""Integration test for FR-03: the full PDF → extract → persist pipeline.

Wires the real components — FR-01 whole-document extraction of the committed
fixture, FR-03 single-call LLM extraction, and SQLite persistence — together.
The "LLM" is a deterministic ``FunctionModel`` that reads the whole report and
returns its list of findings, so the test proves the wiring and the ≥90%
well-formed criterion without any network call (ADR-0047).
"""

from pathlib import Path

import pytest
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select
from tests._extract_helpers import fake_extractor

from revalid.db import IN_MEMORY, FindingVersionRecord, create_db_engine, session_factory
from revalid.domain import Severity
from revalid.extract import build_extraction_agent, extract_report
from revalid.findings import create_finding
from revalid.pdf import read_pdf

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"


def test_pipeline_extracts_and_persists_all_findings() -> None:
    report = read_pdf(FIXTURE.read_bytes())
    agent = build_extraction_agent(FunctionModel(fake_extractor))

    result = extract_report(agent, report)

    # The one call returned four schema-valid findings — 4/4 well-formed (FR-03 ≥90%).
    assert not result.failures
    assert [f.title for f in result.findings] == [
        "Finding 1 — SQL Injection in Login Form",
        "Finding 2 — Reflected Cross-Site Scripting in Search",
        "Finding 3 — Broken Access Control on Basket",
        "Finding 4 — Verbose Error Messages Expose Stack Traces",
    ]
    assert [f.severity for f in result.findings] == [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.HIGH,
        Severity.MEDIUM,
    ]
    # All mandatory FR-03 fields are populated on every finding.
    for finding in result.findings:
        assert finding.impact and finding.attack_vector and finding.description
        assert finding.affected_endpoints and finding.reproduction_steps


def test_extracted_findings_survive_persistence() -> None:
    report = read_pdf(FIXTURE.read_bytes())
    agent = build_extraction_agent(FunctionModel(fake_extractor))
    findings = extract_report(agent, report).findings

    engine = create_db_engine(IN_MEMORY)
    factory = session_factory(engine)
    with factory() as session:
        for finding in findings:
            create_finding(session, finding)
        session.commit()

    with factory() as session:
        rows = list(
            session.scalars(select(FindingVersionRecord).order_by(FindingVersionRecord.finding_id))
        )
    assert len(rows) == 4
    first = rows[0].to_domain()
    assert first.impact == "Attacker-controlled outcome as described."
    assert "/rest/user/login" in first.affected_endpoints
