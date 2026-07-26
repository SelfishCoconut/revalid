"""PDF pentest-report ingestion — whole-document text extraction (FR-01).

This module turns report *bytes* into LLM-ready Markdown text. It is
intentionally **LLM-free**: the semantic step (text -> validated ``Finding``
objects) is FR-03's job (see :mod:`revalid.extract`). Extraction uses
PyMuPDF4LLM in its deterministic *legacy* mode (ADR-0047): the whole document is
rendered to GitHub-flavoured Markdown, so headings, tables and lists survive as
structure the model can read, and the same bytes always produce the same text
(NFR-02). The extractor then hands the **entire** report to the model in one
call — there is no heading segmentation ahead of it (that regex step was removed
in ADR-0047 because it was format-bound and silently failed on unfamiliar
layouts).

Malformed input fails closed with a clear :class:`PdfError` rather than crashing:
a non-PDF (missing ``%PDF-`` header), a structurally corrupt PDF, and a PDF with
no extractable text (scanned/image-only — out of scope, no OCR) are all rejected.
"""

from __future__ import annotations

import pymupdf
import pymupdf4llm
from pydantic import BaseModel, ConfigDict, Field

# Deterministic legacy extraction: pure PyMuPDF text -> Markdown, with no ML
# layout model and no Tesseract OCR. Reproducibility (NFR-02) and a
# dependency-light path matter more here than the layout model's marginal gains;
# OCR is out of scope (image-only reports are rejected, not transcribed) and its
# default-on path hard-fails without a Tesseract data directory. The mode is a
# process-global toggle in pymupdf4llm, so it is set once at import.
pymupdf4llm.use_layout(False)

_PDF_MAGIC = b"%PDF-"


class PdfError(ValueError):
    """Raised when a PDF cannot be read or carries no extractable report text."""


class PdfPage(BaseModel):
    """Text extracted from one page of a report.

    Attributes:
        number: 1-based page number in document order.
        text: Reading-order Markdown of the page (empty if the page has none).
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    text: str = ""


class PdfReport(BaseModel):
    """The full deterministic extraction of a PDF report (FR-01 output).

    Attributes:
        page_count: Number of pages in the source document.
        pages: Per-page extracted Markdown, in document order.
        text: Whole-report Markdown, pages joined in order — the single input
            FR-03's LLM structures into findings in one call.
    """

    model_config = ConfigDict(frozen=True)

    page_count: int = Field(ge=1)
    pages: tuple[PdfPage, ...]
    text: str = Field(min_length=1)


def read_pdf(data: bytes) -> PdfReport:
    """Extract a PDF report to Markdown text, tolerating common layouts (FR-01).

    Args:
        data: Raw bytes of the PDF document.

    Returns:
        The extracted report: per-page and whole-document Markdown text.

    Raises:
        PdfError: If ``data`` is not a PDF, is structurally corrupt, or yields
            no extractable text (e.g. a scanned/image-only document).
    """
    if data[: len(_PDF_MAGIC)] != _PDF_MAGIC:
        raise PdfError("not a PDF (missing %PDF- header)")
    try:
        pages = _extract_pages(data)
    except pymupdf.FileDataError as exc:
        raise PdfError(f"could not parse PDF: {exc}") from exc

    text = "\n\n".join(page.text for page in pages if page.text).strip()
    if not text:
        raise PdfError("no extractable text (is this a scanned or image-only PDF?)")
    return PdfReport(page_count=len(pages), pages=pages, text=text)


def _extract_pages(data: bytes) -> tuple[PdfPage, ...]:
    """Render each page to Markdown with PyMuPDF4LLM (deterministic legacy mode)."""
    # pymupdf ships stubs but its Document constructor is untyped.
    with pymupdf.open(stream=data, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        chunks = pymupdf4llm.to_markdown(document, page_chunks=True, show_progress=False)
    return tuple(
        PdfPage(number=index, text=str(chunk["text"]).strip())
        for index, chunk in enumerate(chunks, start=1)
    )
