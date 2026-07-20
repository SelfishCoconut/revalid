"""LLM extraction of structured findings from report text (FR-03).

Turns the unstructured text/candidates produced by FR-01 (``pdf.py``) into
schema-validated domain :class:`~revalid.domain.Finding` objects using Pydantic
AI (ADR-0002, ADR-0009). Each FR-01 finding candidate is sent to the model,
which must return :class:`ExtractedFinding` objects; output that fails schema
validation is retried by Pydantic AI and, if still invalid, **flagged** as an
:class:`ExtractionFailure` — never silently mapped to a ``Finding`` or persisted
(FR-03's schema-validation gate). The model is injectable so unit tests drive it
with Pydantic AI's ``TestModel``/``FunctionModel`` and never touch the network;
when none is passed, the configured backend is used (``REVALID_LLM_MODEL``,
FR-13/ADR-0010).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import Finding, Severity
from revalid.llm import agent_model_name, resolve_model
from revalid.pdf import FindingCandidate, PdfReport, segment_findings

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


class Person(BaseModel):
    """A person named in the report, with their stated role (#133)."""

    model_config = ConfigDict(frozen=True)

    name: str
    role: str


class ReportMetadata(BaseModel):
    """Document-level metadata extracted from a report (FR-03, #133).

    Every field defaults to empty, so an absent value — or a failed extraction on
    a small local model — yields a blank the operator can fill in, never a guess.
    """

    model_config = ConfigDict(frozen=True)

    product: str = ""
    report_date: str = ""
    author: str = ""
    people: tuple[Person, ...] = ()


_METADATA_INSTRUCTIONS = """\
You extract document-level metadata from a penetration-test report. Return only
what the text actually states — never invent. Set:
- product: the target system, product, or client under test.
- report_date: the date the report was issued, exactly as written (any format).
- author: the person or team who wrote or led the assessment.
- people: everyone named as involved (pentesters, reviewers, contacts), each with
  their role as stated; an empty list if none are named.
Use an empty string for any field the report does not state.\
"""


def build_metadata_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[None, ReportMetadata]:
    """Build the document-metadata extraction agent (FR-03, #133)."""
    return Agent(
        model if model is not None else resolve_model(),
        output_type=ReportMetadata,
        instructions=_METADATA_INSTRUCTIONS,
        retries=_MAX_OUTPUT_RETRIES,
        defer_model_check=True,
    )


def extract_metadata(agent: Agent[None, ReportMetadata], report: PdfReport) -> ReportMetadata:
    """Extract document metadata from a report's opening text (FR-03, #133).

    Best-effort and non-fatal: metadata lives near the top of a report, so only
    the first few thousand characters are sent, and any model or validation
    failure yields empty metadata the operator can edit — it never blocks the
    report from becoming ready.

    Args:
        agent: The metadata agent (from :func:`build_metadata_agent`).
        report: An extracted report from :func:`revalid.pdf.read_pdf`.

    Returns:
        The extracted (or empty-on-failure) :class:`ReportMetadata`.
    """
    try:
        return agent.run_sync(report.text[:6000]).output
    except Exception:
        return ReportMetadata()


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
    model: Model | KnownModelName | str | None = None,
) -> Agent[None, list[ExtractedFinding]]:
    """Build the finding-extraction agent.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the
            configured backend is used (``REVALID_LLM_MODEL``, Claude by
            default — FR-13/ADR-0010); tests pass ``TestModel``/
            ``FunctionModel``.

    Returns:
        An agent whose validated output is a list of :class:`ExtractedFinding`.
    """
    return Agent(
        model if model is not None else resolve_model(),
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
    model_name = agent_model_name(agent)
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
