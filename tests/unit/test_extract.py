"""Unit tests for LLM finding extraction (FR-03).

No network and no real model: Pydantic AI's ``FunctionModel`` drives exact
outputs so we can prove the schema-validation gate (valid → mapped, invalid →
flagged, never persisted).
"""

import asyncio
import contextlib
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from revalid.domain import Severity
from revalid.extract import (
    ExtractedFinding,
    ExtractionRegistry,
    ExtractionReport,
    build_extraction_agent,
    extract_report,
    extract_report_async,
)
from revalid.llm import DEFAULT_MODEL, agent_model_name
from revalid.pdf import PdfPage, PdfReport, segment_findings

_VALID: dict[str, Any] = {
    "title": "SQL Injection in Login",
    "severity": "critical",
    "description": "Login concatenates input into SQL.",
    "impact": "Full authentication bypass.",
    "attack_vector": "Crafted email in the login form.",
    "affected_endpoints": ["/rest/user/login"],
    "reproduction_steps": ["Open /#/login", "Submit ' OR 1=1--"],
}

_CVSS: dict[str, Any] = {
    "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "base_score": 9.8,
    "inferred": False,
}
_MITRE: dict[str, Any] = {"techniques": ["T1190"], "inferred": False}


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


def test_extract_report_cancels_before_the_first_candidate() -> None:
    """A cancel requested up front stops before any model call — nothing extracted (#205)."""
    agent = build_extraction_agent(_model_returning(_VALID))
    result = extract_report(agent, _report("a finding"), should_cancel=lambda: True)
    assert result.cancelled is True
    assert result.findings == ()
    assert result.failures == ()


def test_extract_report_keeps_partial_findings_on_cancel() -> None:
    """A cancel between candidates keeps the ones already extracted (#205)."""
    text = "Finding 1: SQLi\nfirst body\n\nFinding 2: XSS\nsecond body"
    report = PdfReport(page_count=1, pages=(PdfPage(number=1, text=text),), text=text)
    assert len(segment_findings(report)) == 2  # sanity: two candidates, so mid-run cancel is real
    agent = build_extraction_agent(_model_returning(_VALID))
    checks = {"n": 0}

    def should_cancel() -> bool:
        checks["n"] += 1
        return checks["n"] > 1  # allow the first candidate, then stop before the second

    result = extract_report(agent, report, should_cancel=should_cancel)
    assert result.cancelled is True
    assert len(result.findings) == 1  # the first candidate's finding was kept


def test_report_stated_cvss_and_mitre_mapped_verbatim() -> None:
    # FR-19: a CVSS code / ATT&CK techniques stated in the report map through
    # unchanged, with inferred=False marking them as read, not derived.
    stated = {**_VALID, "cvss": _CVSS, "mitre": _MITRE}
    agent = build_extraction_agent(_model_returning(stated))
    [finding] = extract_report(agent, _report("a finding with a stated CVSS")).findings
    assert finding.cvss.vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    assert finding.cvss.base_score == 9.8
    assert finding.cvss.inferred is False
    assert finding.mitre.techniques == ("T1190",)
    assert finding.mitre.inferred is False
    # The taxonomy fields also land in the audit blob (NFR-02).
    assert finding.raw["extracted"]["cvss"]["base_score"] == 9.8


def test_derived_cvss_and_mitre_carry_inferred_provenance() -> None:
    # FR-19: when the report states no CVSS/ATT&CK, the model derives them and
    # sets inferred=True, so the operator can tell generated from stated.
    derived = {
        **_VALID,
        "cvss": {**_CVSS, "inferred": True},
        "mitre": {"techniques": ["T1190", "T1110"], "inferred": True},
    }
    agent = build_extraction_agent(_model_returning(derived))
    [finding] = extract_report(agent, _report("a finding, no CVSS stated")).findings
    assert finding.cvss.inferred is True
    assert finding.mitre.techniques == ("T1190", "T1110")
    assert finding.mitre.inferred is True


def test_absent_cvss_and_mitre_default_to_empty_not_inferred() -> None:
    # If the model omits them entirely, the schema defaults keep the finding
    # valid with empty, non-inferred taxonomy fields (never a fabricated code).
    agent = build_extraction_agent(_model_returning(_VALID))
    [finding] = extract_report(agent, _report("plain finding")).findings
    assert finding.cvss.vector == ""
    assert finding.cvss.base_score is None
    assert finding.cvss.inferred is False
    assert finding.mitre.techniques == ()
    assert finding.mitre.inferred is False


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


class _HangingModel(Model):
    """A model whose request hangs until the task is cancelled (issue #205 interrupt test)."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        await asyncio.sleep(3600)
        raise AssertionError("cancelled before returning")  # pragma: no cover - unreachable

    @property
    def model_name(self) -> str:
        return "hanging"

    @property
    def system(self) -> str:
        return "test"


def test_extract_report_async_returns_cancelled_when_interrupted() -> None:
    """A Stop that cancels the in-flight model call returns a cancelled result (#205)."""
    agent = build_extraction_agent(_HangingModel())
    report = _report("a candidate whose model call never finishes")

    async def drive() -> ExtractionReport:
        task = asyncio.ensure_future(extract_report_async(agent, report))
        await asyncio.sleep(0.05)  # let it reach the awaiting model call
        task.cancel()
        return await task

    result = asyncio.run(drive())
    assert result.cancelled is True
    assert result.findings == ()  # nothing completed before the interrupt


def test_extraction_registry_cancel_interrupts_the_attached_task() -> None:
    """request_cancel aborts the in-flight extraction task cross-thread (#205)."""
    reg = ExtractionRegistry()
    loop = asyncio.new_event_loop()

    async def forever() -> ExtractionReport:
        await asyncio.sleep(3600)
        return ExtractionReport(findings=(), failures=())  # pragma: no cover - unreachable

    task = loop.create_task(forever())
    reg.attach(7, loop, task)
    reg.request_cancel(7)  # schedules the cancel on the attached task
    with contextlib.suppress(asyncio.CancelledError):
        loop.run_until_complete(task)
    assert task.cancelled()
    loop.close()
