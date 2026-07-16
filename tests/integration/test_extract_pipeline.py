"""Integration test for FR-03: the full PDF → extract → persist pipeline.

Wires the real components — FR-01 extraction of the committed fixture, FR-03
per-candidate LLM extraction, and SQLite persistence — together. The "LLM" is a
deterministic ``FunctionModel`` that reads each candidate's text and returns a
complete finding, so the test proves the wiring and the ≥90% well-formed
criterion without any network call.
"""

import re
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select

from revalid.db import IN_MEMORY, FindingVersionRecord, create_db_engine, session_factory
from revalid.domain import Severity
from revalid.extract import build_extraction_agent, extract_report
from revalid.findings import create_finding
from revalid.pdf import read_pdf

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"

_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _candidate_text(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        for part in getattr(message, "parts", ()):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def _fake_extractor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Turn one candidate's text into a complete finding (deterministic stand-in)."""
    text = _candidate_text(messages)
    lowered = text.lower()
    severity = next((s for s in _SEVERITIES if s in lowered), "info")
    endpoints = re.findall(r"/(?:rest|#)[\w/{}?=.#-]*", text)
    steps = [line.strip() for line in text.splitlines() if re.match(r"\d+\.\s", line.strip())]
    finding = {
        "title": text.splitlines()[0].strip(),
        "severity": severity,
        "description": "Extracted from the report excerpt.",
        "impact": "Attacker-controlled outcome as described.",
        "attack_vector": "As described in the reproduction steps.",
        "affected_endpoints": endpoints,
        "reproduction_steps": steps,
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"response": [finding]})]
    )


def test_pipeline_extracts_and_persists_all_findings() -> None:
    report = read_pdf(FIXTURE.read_bytes())
    agent = build_extraction_agent(FunctionModel(_fake_extractor))

    result = extract_report(agent, report)

    # Every candidate produced a schema-valid finding — 4/4 well-formed (FR-03 ≥90%).
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
    agent = build_extraction_agent(FunctionModel(_fake_extractor))
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
