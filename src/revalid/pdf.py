"""PDF pentest-report ingestion — deterministic text extraction (FR-01).

This module turns report *bytes* into text and best-effort finding candidates.
It is intentionally **LLM-free**: the semantic step (text -> validated
``Finding`` objects) is FR-03's job (see ADR-0007 for the seam). Extraction uses
pdfplumber; text inside table cells is surfaced by its text extractor directly,
so table-borne finding data (severity/CWE/endpoint grids) survives without
separate table parsing.

Malformed *and* hostile input fails closed with a clear :class:`PdfError` rather
than crashing: a non-PDF (missing ``%PDF-`` header), a document over the size /
page / extracted-text bounds (decompression-bomb guard), a document that blows a
wall-clock deadline (a single crafted page can hang pdfminer's layout analysis),
a structurally corrupt PDF, and a PDF with no extractable text (scanned/image-only
— out of scope, no OCR) are all rejected. As the untrusted-input trust boundary,
extraction also converts any lower-level parser failure (including ``MemoryError``
from the pdfminer C path) into a ``PdfError``.
"""

from __future__ import annotations

import io
import itertools
import re
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field

_PDF_MAGIC = b"%PDF-"

# Resource bounds against malicious PDFs (decompression bombs, pathological page
# counts): this module is the untrusted-input trust boundary, so it fails closed
# rather than letting pdfminer exhaust memory. Generous for real pentest reports.
_MAX_PDF_BYTES = 25 * 2**20  # 25 MiB input file
_MAX_PAGES = 500  # pages per report
_MAX_TEXT_CHARS = 10 * 2**20  # 10 MiB of cumulative extracted text
_MAX_EXTRACT_SECONDS = 30.0  # wall-clock cap: a single crafted page can hang pdfminer

# Common per-finding heading conventions in pentest reports. Verbose form so no
# code formatter reflows it: whitespace here is ignored, but spaces inside the
# [ \t] character classes are preserved and still match.
_FINDING_HEADING = re.compile(
    r"""
    ^[ \t]* (?: finding | vulnerability | vuln ) [ \t]* [#-]? [ \t]* \d+ \b .* $
    |
    ^[ \t]* f -? \d{1,3} \b .* $
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


class PdfError(ValueError):
    """Raised when a PDF cannot be read or carries no extractable report text."""


class PdfPage(BaseModel):
    """Text extracted from one page of a report.

    Attributes:
        number: 1-based page number in document order.
        text: Reading-order text of the page (empty if the page has none).
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    text: str = ""


class PdfReport(BaseModel):
    """The full deterministic extraction of a PDF report (FR-01 output).

    Attributes:
        page_count: Number of pages in the source document.
        pages: Per-page extracted text, in document order.
        text: Whole-report text, pages joined in order — the input FR-03's LLM
            structures into findings.
    """

    model_config = ConfigDict(frozen=True)

    page_count: int = Field(ge=1)
    pages: tuple[PdfPage, ...]
    text: str = Field(min_length=1)


class FindingCandidate(BaseModel):
    """A raw, unstructured slice of the report likely to describe one finding.

    Produced by best-effort heading segmentation, not semantic parsing: the
    LLM (FR-03) turns these into validated findings.

    Attributes:
        heading: The heading line that opened this candidate (empty when the
            whole document is returned as a single candidate).
        text: The candidate's text, heading included.
    """

    model_config = ConfigDict(frozen=True)

    heading: str = ""
    text: str = Field(min_length=1)


def read_pdf(data: bytes) -> PdfReport:
    """Extract text from a PDF report, tolerating common layouts (FR-01).

    Args:
        data: Raw bytes of the PDF document.

    Returns:
        The extracted report: per-page and whole-document text.

    Raises:
        PdfError: If ``data`` is not a PDF, exceeds the size/page/text bounds,
            is structurally corrupt, or yields no extractable text (e.g. a
            scanned/image-only document).
    """
    if data[: len(_PDF_MAGIC)] != _PDF_MAGIC:
        raise PdfError("not a PDF (missing %PDF- header)")
    if len(data) > _MAX_PDF_BYTES:
        raise PdfError(f"PDF too large ({len(data)} bytes > {_MAX_PDF_BYTES} limit)")
    try:
        pages = _extract_pages(data)
    except PdfError:
        raise  # our own bounds already carry a clear message — don't relabel
    except Exception as exc:
        # Untrusted-input boundary: convert *any* parser failure — including
        # MemoryError/RecursionError/SystemError from the pdfminer C path — into
        # a typed PdfError so the contract ("reject, never crash") holds.
        raise PdfError(f"could not parse PDF: {exc}") from exc

    text = "\n\n".join(page.text for page in pages if page.text).strip()
    if not text:
        raise PdfError("no extractable text (is this a scanned or image-only PDF?)")
    return PdfReport(page_count=len(pages), pages=pages, text=text)


class _ExtractionTimeoutError(Exception):
    """Internal: the extraction wall-clock deadline fired."""


@contextmanager
def _extraction_deadline(seconds: float) -> Iterator[dict[str, bool]]:
    """Best-effort wall-clock cap on extraction (main thread + POSIX only).

    Yields a one-key flag dict set ``True`` if the deadline fired, letting the
    caller tell a timeout (which pdfplumber may re-wrap into its own exception
    type) apart from a genuine parse error. Off the main thread — e.g. under a
    future threadpool-served endpoint — signal timers are unavailable, so the
    guard is skipped and the size/page/text bounds are the only defence; that
    path should run extraction in a subprocess instead (follow-up).

    Args:
        seconds: Deadline in wall-clock seconds.

    Yields:
        A ``{"fired": bool}`` flag, updated if the deadline expires.
    """
    hit = {"fired": False}
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield hit
        return

    def _fire(signum: int, frame: FrameType | None) -> None:
        hit["fired"] = True
        raise _ExtractionTimeoutError

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield hit
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _extract_pages(data: bytes) -> tuple[PdfPage, ...]:
    """Open with pdfplumber and pull each page's text, bounded against bombs."""
    pages: list[PdfPage] = []
    total_chars = 0
    with _extraction_deadline(_MAX_EXTRACT_SECONDS) as deadline:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    if index > _MAX_PAGES:
                        raise PdfError(f"too many pages (> {_MAX_PAGES}); refusing to parse")
                    text = (page.extract_text() or "").strip()
                    total_chars += len(text)
                    if total_chars > _MAX_TEXT_CHARS:
                        raise PdfError(f"extracted text exceeds {_MAX_TEXT_CHARS}-char limit")
                    pages.append(PdfPage(number=index, text=text))
        except PdfError:
            raise
        except Exception as exc:
            if deadline["fired"]:
                raise PdfError(
                    f"extraction timed out (> {_MAX_EXTRACT_SECONDS:g}s); refusing to parse"
                ) from exc
            raise
    return tuple(pages)


def segment_findings(report: PdfReport) -> tuple[FindingCandidate, ...]:
    """Split a report into raw finding candidates by heading (best effort).

    Splits the text at recognised finding-heading lines ("Finding N", "F-01",
    …); any preamble before the first heading (cover page, table of contents) is
    dropped. When no heading matches, the whole document is returned as a single
    candidate so downstream extraction always has something to work with.

    Args:
        report: An extracted report from :func:`read_pdf`.

    Returns:
        One candidate per detected finding section, in document order.
    """
    starts = [match.start() for match in _FINDING_HEADING.finditer(report.text)]
    if not starts:
        return (FindingCandidate(heading="", text=report.text),)

    bounds = [*starts, len(report.text)]
    candidates = []
    for begin, end in itertools.pairwise(bounds):
        block = report.text[begin:end].strip()
        if block:
            heading = block.splitlines()[0].strip()
            candidates.append(FindingCandidate(heading=heading, text=block))
    return tuple(candidates)
