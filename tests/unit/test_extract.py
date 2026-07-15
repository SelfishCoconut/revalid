"""Unit tests for LLM finding extraction (FR-03).

No network and no real model: Pydantic AI's ``FunctionModel`` drives exact
outputs so we can prove the schema-validation gate (valid → mapped, invalid →
flagged, never persisted).
"""

from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.domain import Severity
from revalid.extract import (
    ExtractedFinding,
    build_extraction_agent,
    extract_report,
)
from revalid.llm import DEFAULT_MODEL, agent_model_name
from revalid.pdf import PdfPage, PdfReport

_VALID: dict[str, Any] = {
    "title": "SQL Injection in Login",
    "severity": "critical",
    "description": "Login concatenates input into SQL.",
    "impact": "Full authentication bypass.",
    "attack_vector": "Crafted email in the login form.",
    "affected_endpoints": ["/rest/user/login"],
    "reproduction_steps": ["Open /#/login", "Submit ' OR 1=1--"],
}


def _report(text: str) -> PdfReport:
    """One-candidate report wrapping raw text (no headings → whole doc)."""
    return PdfReport(page_count=1, pages=(PdfPage(number=1, text=text),), text=text)


def _model_returning(*findings: dict[str, Any]) -> FunctionModel:
    """A model that always emits ``findings`` as its structured output."""

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": list(findings)})]
        )

    return FunctionModel(respond)


def test_valid_output_maps_to_domain_findings() -> None:
    agent = build_extraction_agent(_model_returning(_VALID))
    result = extract_report(agent, _report("a finding without a heading"))

    assert not result.failures
    [finding] = result.findings
    assert finding.title == "SQL Injection in Login"
    assert finding.severity is Severity.CRITICAL
    assert finding.impact == "Full authentication bypass."
    assert finding.attack_vector == "Crafted email in the login form."
    assert finding.affected_endpoints == ("/rest/user/login",)
    assert finding.reproduction_steps == ("Open /#/login", "Submit ' OR 1=1--")


def test_extraction_lineage_recorded_in_raw() -> None:
    # NFR-02: model name + source text captured so a finding is auditable.
    agent = build_extraction_agent(_model_returning(_VALID))
    [finding] = extract_report(agent, _report("some finding text")).findings
    assert finding.raw["source"] == "pdf_extraction"
    assert finding.raw["model"].startswith("function")
    assert finding.raw["source_text"] == "some finding text"
    assert finding.raw["extracted"]["title"] == "SQL Injection in Login"


def test_multiple_findings_from_one_candidate() -> None:
    # A candidate with no headings (whole document) can yield several findings.
    second = {**_VALID, "title": "Reflected XSS", "severity": "high"}
    agent = build_extraction_agent(_model_returning(_VALID, second))
    result = extract_report(agent, _report("two findings, no headings"))
    assert [f.title for f in result.findings] == ["SQL Injection in Login", "Reflected XSS"]


def test_invalid_output_is_flagged_never_persisted() -> None:
    # Unknown severity fails schema validation; after retries it must be flagged
    # as a failure, not mapped to a (partial) finding. This is the FR-03 gate.
    bad = {**_VALID, "severity": "catastrophic"}
    agent = build_extraction_agent(_model_returning(bad))
    result = extract_report(agent, _report("Finding 9 — bad severity"))

    assert result.findings == ()
    [failure] = result.failures
    assert failure.heading == "Finding 9 — bad severity"
    assert failure.source_text == "Finding 9 — bad severity"
    assert "retr" in failure.error.lower()


def test_missing_mandatory_field_is_flagged() -> None:
    incomplete = {k: v for k, v in _VALID.items() if k != "impact"}
    agent = build_extraction_agent(_model_returning(incomplete))
    result = extract_report(agent, _report("Finding 3 — missing impact"))
    assert result.findings == ()
    assert len(result.failures) == 1


def test_extracted_finding_schema_requires_title() -> None:
    with pytest.raises(ValueError, match="title"):
        ExtractedFinding(
            title="",
            severity=Severity.LOW,
            description="",
            impact="",
            attack_vector="",
            affected_endpoints=(),
            reproduction_steps=(),
        )


def test_default_agent_targets_local_first_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model-agnostic build, local-first default (ADR-0021); constructs offline.
    monkeypatch.delenv("REVALID_LLM_MODEL", raising=False)
    agent = build_extraction_agent()
    assert agent.model == DEFAULT_MODEL
    # NFR-02: the model name recorded in lineage is the configured string.
    assert agent_model_name(agent) == DEFAULT_MODEL
