"""Unit tests for the opt-in CVSS/ATT&CK enrichment pass (FR-19, issue #233).

The PDF door gets its taxonomy inside extraction; the FR-02 JSON and manual
doors are LLM-free, so for them enrichment is a separate pass the operator opts
into. What is pinned here is the part that carries the design: enrichment fills
only what is *empty*, everything it fills is flagged ``inferred``, and a model
that misbehaves costs the taxonomy — never the finding.

No network and no real model: Pydantic AI's ``FunctionModel``/``TestModel``
drive exact outputs.
"""

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.domain import CvssCode, Finding, MitreMapping, Severity
from revalid.extract import (
    FindingTaxonomy,
    apply_taxonomy,
    build_taxonomy_agent,
    enrich_findings,
    taxonomy_prompt,
)

_DERIVED: dict[str, Any] = {
    "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "cvss_base_score": 9.8,
    "mitre_techniques": ["T1190"],
}


def _finding(**overrides: Any) -> Finding:
    base: dict[str, Any] = {
        "title": "SQL injection in login",
        "severity": Severity.HIGH,
        "description": "Login concatenates input into SQL.",
        "impact": "Authentication bypass.",
        "attack_vector": "Crafted email field.",
    }
    return Finding(**{**base, **overrides})


def _model_returning(taxonomy: dict[str, Any]) -> FunctionModel:
    """A model that always emits ``taxonomy`` as its structured output."""

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=taxonomy)])

    return FunctionModel(respond)


def _model_returning_garbage() -> FunctionModel:
    """A model whose output never satisfies the schema, exhausting the retries."""

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"cvss_base_score": "not a number"})]
        )

    return FunctionModel(respond)


def test_enrichment_fills_an_empty_taxonomy_and_flags_it_inferred() -> None:
    agent = build_taxonomy_agent(_model_returning(_DERIVED))

    report = enrich_findings(agent, [_finding()])

    [finding] = report.findings
    assert finding.cvss.vector == _DERIVED["cvss_vector"]
    assert finding.cvss.base_score == 9.8
    assert finding.cvss.inferred is True
    assert finding.mitre.techniques == ("T1190",)
    assert finding.mitre.inferred is True
    assert (report.enriched, report.failed) == (1, 0)


def test_enrichment_never_overwrites_a_stated_cvss() -> None:
    """A code copied from the source outranks anything the model derives."""
    stated = CvssCode(vector="CVSS:3.1/AV:L/AC:H", base_score=3.1, inferred=False)
    agent = build_taxonomy_agent(_model_returning(_DERIVED))

    [finding] = enrich_findings(agent, [_finding(cvss=stated)]).findings

    assert finding.cvss == stated
    assert finding.mitre.techniques == ("T1190",)  # the empty half is still filled


def test_enrichment_never_overwrites_stated_techniques() -> None:
    stated = MitreMapping(techniques=("T1059",), inferred=False)
    agent = build_taxonomy_agent(_model_returning(_DERIVED))

    [finding] = enrich_findings(agent, [_finding(mitre=stated)]).findings

    assert finding.mitre == stated


def test_a_fully_stated_finding_is_returned_unchanged_and_not_counted() -> None:
    already = _finding(
        cvss=CvssCode(vector="CVSS:3.1/AV:N", inferred=False),
        mitre=MitreMapping(techniques=("T1190",), inferred=False),
    )
    agent = build_taxonomy_agent(_model_returning(_DERIVED))

    report = enrich_findings(agent, [already])

    assert report.findings == (already,)
    assert (report.enriched, report.failed) == (0, 0)


def test_an_empty_derivation_leaves_the_finding_bare_rather_than_guessing() -> None:
    """ "I cannot assess this" must record no taxonomy, not a fabricated one."""
    agent = build_taxonomy_agent(
        _model_returning({"cvss_vector": "", "cvss_base_score": None, "mitre_techniques": []})
    )

    report = enrich_findings(agent, [_finding()])

    [finding] = report.findings
    assert finding.cvss.vector == ""
    assert finding.mitre.techniques == ()
    assert (report.enriched, report.failed) == (0, 0)


def test_a_misbehaving_model_costs_the_taxonomy_never_the_finding() -> None:
    """The import must survive a model that cannot produce a valid taxonomy."""
    original = _finding()
    agent = build_taxonomy_agent(_model_returning_garbage())

    report = enrich_findings(agent, [original])

    assert report.findings == (original,)  # unchanged, still importable
    assert (report.enriched, report.failed) == (0, 1)


def test_one_bad_finding_does_not_stop_the_rest() -> None:
    """A failure is per-finding: the batch keeps going and the count is honest."""
    calls: list[int] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        calls.append(1)
        args = {"cvss_base_score": "nope"} if len(calls) <= 3 else _DERIVED
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])

    agent = build_taxonomy_agent(FunctionModel(respond))

    report = enrich_findings(agent, [_finding(title="first"), _finding(title="second")])

    assert (report.enriched, report.failed) == (1, 1)
    assert tuple(f.title for f in report.findings) == ("first", "second")  # order preserved


def test_taxonomy_output_has_no_way_to_claim_a_source_stated_it() -> None:
    """Provenance is server-stamped: the model's schema carries no `inferred` field."""
    assert "inferred" not in FindingTaxonomy.model_fields


def test_apply_taxonomy_returns_the_same_object_when_nothing_to_fill() -> None:
    already = _finding(
        cvss=CvssCode(vector="CVSS:3.1/AV:N"),
        mitre=MitreMapping(techniques=("T1190",)),
    )
    assert apply_taxonomy(already, FindingTaxonomy(**_DERIVED)) is already


def test_prompt_carries_the_fields_the_classification_is_judged_on() -> None:
    prompt = taxonomy_prompt(_finding(affected_endpoints=("/rest/user/login",)))

    assert "SQL injection in login" in prompt
    assert "Login concatenates input into SQL." in prompt
    assert "Authentication bypass." in prompt
    assert "/rest/user/login" in prompt


def test_enriching_nothing_calls_no_model() -> None:
    def explode(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise AssertionError("the model must not be called for an empty batch")

    report = enrich_findings(build_taxonomy_agent(FunctionModel(explode)), [])

    assert report.findings == ()
    assert (report.enriched, report.failed) == (0, 0)
