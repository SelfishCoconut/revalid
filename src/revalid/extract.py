"""LLM extraction of structured findings from report text (FR-03).

Turns the unstructured text/candidates produced by FR-01 (``pdf.py``) into
schema-validated domain :class:`~revalid.domain.Finding` objects using Pydantic
AI (ADR-0002, ADR-0009). Each FR-01 finding candidate is sent to the model,
which must return :class:`ExtractedFinding` objects; output that fails schema
validation is retried by Pydantic AI and, if still invalid, **flagged** as an
:class:`ExtractionFailure` — never silently mapped to a ``Finding`` or persisted
(FR-03's schema-validation gate). The model is injectable so unit tests drive it
with Pydantic AI's ``TestModel``/``FunctionModel`` and never touch the network.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import Finding, Severity
from revalid.pdf import FindingCandidate, PdfReport, segment_findings

# Claude primary (ADR-0002); swappable per FR-13. Deferred model check keeps
# construction offline-safe and lets FR-13 pass arbitrary provider strings.
DEFAULT_MODEL: KnownModelName = "anthropic:claude-sonnet-5"
_MAX_OUTPUT_RETRIES = 2

_INSTRUCTIONS = """\
You extract penetration-test findings from a slice of a report and return them
as structured data. Extract only findings actually present in the text — never
invent or embellish. Produce one entry per distinct finding.

For each finding set:
- title: the finding's name.
- severity: exactly one of info, low, medium, high, critical (normalise the
  report's wording to the closest level).
- description: what the vulnerability is.
- impact: what an attacker gains or the business consequence.
- attack_vector: how the vulnerability is reached and exploited.
- affected_endpoints: the URLs or endpoint paths it applies to.
- reproduction_steps: the ordered steps to reproduce it, one step per item.

If the text does not state a field, use an empty string (or an empty list for
the list fields) rather than guessing.\
"""


class ExtractedFinding(BaseModel):
    """One finding exactly as the model must return it — the FR-03 gate.

    Every field is required, so the model must account for all of them; Pydantic
    AI validates the tool output against this schema and retries on mismatch.
    Strings may be empty when the report omits a field; ``title`` may not.
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    severity: Severity
    description: str
    impact: str
    attack_vector: str
    affected_endpoints: tuple[str, ...]
    reproduction_steps: tuple[str, ...]


class ExtractionFailure(BaseModel):
    """A candidate whose extraction never validated — flagged, not persisted.

    Attributes:
        heading: The source candidate's heading (may be empty).
        error: The reason extraction failed (retries exhausted, etc.).
        source_text: The candidate text, kept so the failure is auditable and
            re-runnable.
    """

    model_config = ConfigDict(frozen=True)

    heading: str
    error: str
    source_text: str


class ExtractionReport(BaseModel):
    """Outcome of extracting a whole report.

    Attributes:
        findings: Schema-valid findings, safe to persist.
        failures: Candidates that failed the validation gate, for review.
    """

    model_config = ConfigDict(frozen=True)

    findings: tuple[Finding, ...]
    failures: tuple[ExtractionFailure, ...]


def build_extraction_agent(
    model: Model | KnownModelName | str = DEFAULT_MODEL,
) -> Agent[None, list[ExtractedFinding]]:
    """Build the finding-extraction agent.

    Args:
        model: A Pydantic AI model instance or name. Defaults to Claude
            (:data:`DEFAULT_MODEL`); tests pass ``TestModel``/``FunctionModel``.

    Returns:
        An agent whose validated output is a list of :class:`ExtractedFinding`.
    """
    return Agent(
        model,
        output_type=list[ExtractedFinding],
        instructions=_INSTRUCTIONS,
        retries=_MAX_OUTPUT_RETRIES,
        defer_model_check=True,
    )


def extract_report(
    agent: Agent[None, list[ExtractedFinding]], report: PdfReport
) -> ExtractionReport:
    """Extract structured findings from every candidate in a report (FR-03).

    Runs one model call per FR-01 finding candidate. Valid output is mapped to
    domain findings; a candidate whose output never passes the schema gate is
    flagged instead of persisted.

    Args:
        agent: The extraction agent (from :func:`build_extraction_agent`).
        report: An extracted report from :func:`revalid.pdf.read_pdf`.

    Returns:
        The valid findings and the flagged failures.
    """
    model_name = _model_name(agent)
    findings: list[Finding] = []
    failures: list[ExtractionFailure] = []
    for candidate in segment_findings(report):
        try:
            result = agent.run_sync(candidate.text)
        except UnexpectedModelBehavior as exc:
            failures.append(
                ExtractionFailure(
                    heading=candidate.heading, error=str(exc), source_text=candidate.text
                )
            )
            continue
        findings.extend(_to_finding(item, candidate, model_name) for item in result.output)
    return ExtractionReport(findings=tuple(findings), failures=tuple(failures))


def _to_finding(
    extracted: ExtractedFinding, candidate: FindingCandidate, model_name: str
) -> Finding:
    """Map a validated ``ExtractedFinding`` to a domain finding with lineage."""
    return Finding(
        title=extracted.title,
        severity=extracted.severity,
        description=extracted.description,
        impact=extracted.impact,
        attack_vector=extracted.attack_vector,
        affected_endpoints=extracted.affected_endpoints,
        reproduction_steps=extracted.reproduction_steps,
        raw={
            "source": "pdf_extraction",
            "model": model_name,
            "candidate_heading": candidate.heading,
            "source_text": candidate.text,
            "extracted": extracted.model_dump(),
        },
    )


def _model_name(agent: Agent[None, list[ExtractedFinding]]) -> str:
    """Best-effort stable model identifier for the audit trail (NFR-02)."""
    model = agent.model
    if isinstance(model, str):
        return model
    return getattr(model, "model_name", str(model))
