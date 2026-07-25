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

import asyncio
import threading
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import CvssCode, Finding, MitreMapping, Severity
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
- cvss: the CVSS base code. If the report states a CVSS vector or score, copy it
  exactly and set cvss.inferred to false. If the report states none, derive a
  best-estimate CVSS v3.1 base vector (and its 0-10 base score) from the
  finding's nature and set cvss.inferred to true. Leave the vector empty only if
  the finding cannot be assessed at all.
- mitre: the MITRE ATT&CK technique IDs the finding maps to (e.g. T1190, T1110).
  If the report states them, copy them and set mitre.inferred to false;
  otherwise infer the most applicable technique IDs and set mitre.inferred to
  true.

For the descriptive fields (description, impact, attack_vector,
affected_endpoints, reproduction_steps), if the text does not state a value use
an empty string (or an empty list) rather than guessing. The cvss and mitre
fields are the deliberate exception: derive them from the finding when the report
is silent, and mark them inferred as above.\
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
    cvss: CvssCode = Field(default_factory=CvssCode)
    mitre: MitreMapping = Field(default_factory=MitreMapping)


_TAXONOMY_INSTRUCTIONS = """\
You classify a single penetration-test finding. You are given the finding as it
was already recorded; return only its taxonomy, nothing else.

- cvss_vector: a best-estimate CVSS v3.1 base vector for the finding
  (e.g. CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H).
- cvss_base_score: that vector's 0-10 base score.
- mitre_techniques: the MITRE ATT&CK technique IDs the finding maps to
  (e.g. T1190, T1110), most applicable first.

Judge only from the finding's title, description, impact and attack vector. If
the finding genuinely cannot be assessed, return an empty vector and an empty
technique list rather than guessing wildly — an empty answer is recorded as "no
taxonomy", which is honest, whereas a wild guess is recorded as a real
assessment.

All three fields are required: answer every one of them, even if the answer is
empty. Do not omit a field.\
"""


class FindingTaxonomy(BaseModel):
    """A finding's derived CVSS + ATT&CK classification (FR-19, opt-in enrichment).

    Deliberately **without** an ``inferred`` flag: everything this model returns is
    by definition the model's own derivation, so provenance is stamped server-side
    (:func:`apply_taxonomy` sets ``inferred=True``) and the model has no way to
    express "the report stated this". That is the same rule the finding editor
    follows — a client, human or machine, never asserts provenance (ADR-0037).

    Every field is **required**, for the same reason :class:`ExtractedFinding`'s
    are: the model must account for all of them, and Pydantic AI retries when it
    does not. With defaults, ``{}`` was trivially valid — a small model could
    return nothing, no retry would fire, and the result was indistinguishable from
    "assessed and found nothing to say" (issue #241, seen live on a 9b local
    model). An unassessable finding is still expressible, as an explicit empty
    vector and empty technique list; it just has to be *chosen* rather than
    reached by omission.
    """

    model_config = ConfigDict(frozen=True)

    cvss_vector: str
    cvss_base_score: float | None
    mitre_techniques: tuple[str, ...]


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
        cancelled: Whether extraction stopped early because the operator asked it
            to (issue #205). ``findings``/``failures`` then hold only the candidates
            processed before the stop — a partial, still-persistable result.
    """

    model_config = ConfigDict(frozen=True)

    findings: tuple[Finding, ...]
    failures: tuple[ExtractionFailure, ...]
    cancelled: bool = False


class EnrichmentReport(BaseModel):
    """Outcome of an opt-in taxonomy enrichment pass (FR-19, issue #233).

    Attributes:
        findings: The findings in input order, enriched where possible. A finding
            whose model call failed is present, unchanged.
        enriched: How many findings actually gained a CVSS vector or ATT&CK
            techniques. Lower than ``len(findings)`` when some already had them.
        failed: How many model calls failed schema validation. Non-zero means the
            import succeeded but part of the taxonomy is missing — surfaced to the
            operator rather than swallowed.
    """

    model_config = ConfigDict(frozen=True)

    findings: tuple[Finding, ...]
    enriched: int = 0
    failed: int = 0


class ExtractionRegistry:
    """Process-local cancel flags for in-flight extractions (issue #205).

    Extraction runs one model call per finding candidate as a background task
    (:func:`~revalid.app.run_extraction`). This lets the request thread flag a
    report so that worker settles cooperatively — the loop checks the flag between
    candidates. Thread-safe: the flag is written by the request thread and read by
    the extraction worker.

    A flag carries a *reason*: ``"operator"`` (a Stop — keep whatever was extracted)
    or ``"deleted"`` (the report is being removed — discard the partial result). A
    pending delete always wins over a Stop, since the row is going away.

    Beyond the flag, the registry holds the event loop + task of an in-flight
    extraction (attached by the worker) so a cancel can *interrupt* the current
    model call cross-thread — polling the flag alone would only stop between
    candidates, which never helps when a single candidate's call wedges.
    """

    def __init__(self) -> None:
        """Start with no extraction flagged."""
        self._lock = threading.Lock()
        self._reasons: dict[int, str] = {}
        self._runs: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Task[ExtractionReport]]] = {}

    def request_cancel(self, report_id: int, reason: str = "operator") -> None:
        """Flag ``report_id`` for cancellation and interrupt its in-flight call.

        Records the reason (a ``"deleted"`` flag always wins over an operator Stop)
        and, if an extraction task is attached, cancels it cross-thread so the
        current model call aborts immediately rather than at the next candidate.
        """
        with self._lock:
            if self._reasons.get(report_id) != "deleted":
                self._reasons[report_id] = reason
            run = self._runs.get(report_id)
        if run is not None:
            loop, task = run
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:  # the loop is already closing — the run is ending anyway
                pass

    def cancel_reason(self, report_id: int) -> str | None:
        """Return the cancel reason flagged for ``report_id``, or ``None`` if not flagged."""
        with self._lock:
            return self._reasons.get(report_id)

    def attach(
        self,
        report_id: int,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task[ExtractionReport],
    ) -> None:
        """Register the loop + task of the extraction now running for ``report_id``."""
        with self._lock:
            self._runs[report_id] = (loop, task)

    def clear(self, report_id: int) -> None:
        """Drop any flag + run handle for ``report_id`` (the worker settled)."""
        with self._lock:
            self._reasons.pop(report_id, None)
            self._runs.pop(report_id, None)


def build_taxonomy_agent(
    model: Model | KnownModelName | str | None = None,
) -> Agent[None, FindingTaxonomy]:
    """Build the opt-in CVSS/ATT&CK enrichment agent (FR-19, issue #233).

    The PDF door gets its taxonomy inside the extraction call itself. The FR-02
    JSON and manual doors are deliberately LLM-free, so for them enrichment is a
    *separate, opt-in* pass driven by this agent — one call per finding, only when
    the operator asks for it.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the configured
            backend is used (FR-13/ADR-0010); tests pass ``TestModel``/
            ``FunctionModel``.

    Returns:
        An agent whose validated output is one :class:`FindingTaxonomy`.
    """
    return Agent(
        model if model is not None else resolve_model(),
        output_type=FindingTaxonomy,
        instructions=_TAXONOMY_INSTRUCTIONS,
        retries=_MAX_OUTPUT_RETRIES,
        defer_model_check=True,
    )


def taxonomy_prompt(finding: Finding) -> str:
    """Render the finding as the prompt the taxonomy agent classifies."""
    return (
        f"Title: {finding.title}\n"
        f"Severity: {finding.severity.value}\n"
        f"Description: {finding.description}\n"
        f"Impact: {finding.impact}\n"
        f"Attack vector: {finding.attack_vector}\n"
        f"Affected endpoints: {', '.join(finding.affected_endpoints)}"
    )


def apply_taxonomy(finding: Finding, taxonomy: FindingTaxonomy) -> Finding:
    """Fill a finding's empty CVSS/ATT&CK from ``taxonomy``, flagged as inferred.

    **Never overwrites a stated value.** A finding that already carries a CVSS
    vector or ATT&CK techniques — mapped verbatim from a DefectDojo export, or
    typed by the operator — keeps them exactly, with their existing provenance.
    Only the empty fields are filled, and what this fills is always
    ``inferred=True``: it is the model's derivation, never a source's claim.

    Args:
        finding: The finding to enrich.
        taxonomy: The model's derived classification.

    Returns:
        The finding with previously-empty taxonomy fields filled, or the original
        object unchanged when there was nothing to fill.
    """
    update: dict[str, CvssCode | MitreMapping] = {}
    if not finding.cvss.vector and taxonomy.cvss_vector:
        update["cvss"] = CvssCode(
            vector=taxonomy.cvss_vector, base_score=taxonomy.cvss_base_score, inferred=True
        )
    if not finding.mitre.techniques and taxonomy.mitre_techniques:
        update["mitre"] = MitreMapping(techniques=taxonomy.mitre_techniques, inferred=True)
    return finding.model_copy(update=update) if update else finding


async def enrich_findings_async(
    agent: Agent[None, FindingTaxonomy], findings: Sequence[Finding]
) -> EnrichmentReport:
    """Derive the missing CVSS/ATT&CK for each finding, one model call apiece.

    A finding whose call fails schema validation (after Pydantic AI's retries) is
    **left exactly as it was** and counted in ``failed`` rather than raising: an
    import must not be lost because a small local model could not produce a CVSS
    vector. The count is returned so the caller can surface it — a silently
    unenriched import would look identical to one the operator never asked to
    enrich.

    Args:
        agent: The agent from :func:`build_taxonomy_agent`.
        findings: The findings to enrich, in order.

    Returns:
        The findings in the same order, plus how many were enriched and how many
        model calls failed.
    """
    out: list[Finding] = []
    enriched = 0
    failed = 0
    for finding in findings:
        try:
            result = await agent.run(taxonomy_prompt(finding))
        except UnexpectedModelBehavior:
            failed += 1
            out.append(finding)
            continue
        updated = apply_taxonomy(finding, result.output)
        enriched += updated is not finding
        out.append(updated)
    return EnrichmentReport(findings=tuple(out), enriched=enriched, failed=failed)


def enrich_findings(
    agent: Agent[None, FindingTaxonomy], findings: Sequence[Finding]
) -> EnrichmentReport:
    """Synchronous wrapper over :func:`enrich_findings_async` (the request path)."""
    return asyncio.run(enrich_findings_async(agent, findings))


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


def _never_cancel() -> bool:
    """Default ``should_cancel``: extraction always runs to completion."""
    return False


async def extract_report_async(
    agent: Agent[None, list[ExtractedFinding]],
    report: PdfReport,
    should_cancel: Callable[[], bool] = _never_cancel,
) -> ExtractionReport:
    """Extract structured findings from every candidate in a report (FR-03), async.

    Runs one model call per FR-01 finding candidate via ``await agent.run`` — the
    async path — so the run can be *interrupted* mid-call (issue #205): cancelling
    the task cancels the in-flight HTTP request, which is what makes Stop work even
    when a local model wedges on one candidate. ``should_cancel`` is also polled
    between candidates for the graceful case. Either way, the candidates processed
    so far are returned with ``cancelled=True`` so the caller keeps the partial
    result. Valid output is mapped to domain findings; output that never passes the
    schema gate is flagged instead of persisted.

    Args:
        agent: The extraction agent (from :func:`build_extraction_agent`).
        report: An extracted report from :func:`revalid.pdf.read_pdf`.
        should_cancel: Returns ``True`` when the operator has asked to stop; polled
            between candidates. The task may also be cancelled mid-call.

    Returns:
        The valid findings and the flagged failures, with ``cancelled`` set when the
        run stopped early.
    """
    model_name = agent_model_name(agent)
    findings: list[Finding] = []
    failures: list[ExtractionFailure] = []
    for candidate in segment_findings(report):
        if should_cancel():
            return ExtractionReport(
                findings=tuple(findings), failures=tuple(failures), cancelled=True
            )
        try:
            result = await agent.run(candidate.text)
        except asyncio.CancelledError:
            # The operator interrupted this candidate's model call (Stop / delete):
            # keep what completed and report the stop.
            return ExtractionReport(
                findings=tuple(findings), failures=tuple(failures), cancelled=True
            )
        except UnexpectedModelBehavior as exc:
            failures.append(
                ExtractionFailure(
                    heading=candidate.heading, error=str(exc), source_text=candidate.text
                )
            )
            continue
        findings.extend(_to_finding(item, candidate, model_name) for item in result.output)
    return ExtractionReport(findings=tuple(findings), failures=tuple(failures))


def extract_report(
    agent: Agent[None, list[ExtractedFinding]],
    report: PdfReport,
    should_cancel: Callable[[], bool] = _never_cancel,
) -> ExtractionReport:
    """Synchronous wrapper over :func:`extract_report_async` (tests, offline demos).

    The production path (:func:`~revalid.app.run_extraction`) drives the async form
    directly on a cancellable loop so a Stop can interrupt it; this wrapper runs it
    to completion on a throwaway loop for callers that do not need cancellation.
    """
    return asyncio.run(extract_report_async(agent, report, should_cancel))


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
        cvss=extracted.cvss,
        mitre=extracted.mitre,
        raw={
            "source": "pdf_extraction",
            "model": model_name,
            "candidate_heading": candidate.heading,
            "source_text": candidate.text,
            "extracted": extracted.model_dump(),
        },
    )
