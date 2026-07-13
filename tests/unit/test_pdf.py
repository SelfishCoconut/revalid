"""Unit tests for PDF ingestion (FR-01). Pure: no disk, no real report binary.

Error paths use in-memory byte strings; segmentation is tested on hand-built
``PdfReport`` objects. End-to-end extraction of the real fixture lives in the
integration tier.
"""

import pytest

from revalid.pdf import (
    FindingCandidate,
    PdfError,
    PdfPage,
    PdfReport,
    read_pdf,
    segment_findings,
)


def _assemble_pdf(objs: list[bytes]) -> bytes:
    """Wrap body objects (numbered from 1) in a valid header/xref/trailer."""
    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objs:
        offsets.append(len(pdf))
        pdf += obj
    xref_pos = len(pdf)
    size = len(objs) + 1
    xref = b"xref\n0 %d\n0000000000 65535 f \n" % size
    for off in offsets:
        xref += b"%010d 00000 n \n" % off
    pdf += xref
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_pos)
    return pdf


def _blank_pdf() -> bytes:
    """A minimal, valid single-page PDF that carries no text."""
    return _assemble_pdf(
        [
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n",
        ]
    )


def _text_pdf(message: bytes) -> bytes:
    """A minimal, valid single-page PDF whose page renders ``message`` in Helvetica."""
    stream = b"BT /F1 24 Tf 72 720 Td (" + message + b") Tj ET"
    return _assemble_pdf(
        [
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n",
            b"4 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (len(stream), stream),
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        ]
    )


def _report(text: str) -> PdfReport:
    """Wrap raw text as a one-page report for segmentation tests."""
    return PdfReport(page_count=1, pages=(PdfPage(number=1, text=text),), text=text)


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (b"", "not a PDF"),
        (b"this is plainly not a pdf", "not a PDF"),
        (b"%PDF-1.4\nbut the body is garbage with no objects", "could not parse"),
        (_blank_pdf(), "no extractable text"),
    ],
)
def test_read_pdf_rejects_bad_input(data: bytes, match: str) -> None:
    with pytest.raises(PdfError, match=match):
        read_pdf(data)


def test_read_pdf_extracts_text() -> None:
    report = read_pdf(_text_pdf(b"Finding 1 SQL Injection"))
    assert report.page_count == 1
    assert report.pages[0].number == 1
    assert "Finding 1 SQL Injection" in report.text


def test_segments_by_finding_heading() -> None:
    text = (
        "Cover page and table of contents\n"
        "Finding 1 — SQL Injection\n"
        "Severity: Critical\n"
        "Finding 2 — Reflected XSS\n"
        "Severity: High\n"
    )
    candidates = segment_findings(_report(text))
    assert [c.heading for c in candidates] == [
        "Finding 1 — SQL Injection",
        "Finding 2 — Reflected XSS",
    ]
    # Preamble before the first heading is dropped.
    assert "table of contents" not in candidates[0].text
    # Each candidate carries its own body.
    assert "Severity: Critical" in candidates[0].text
    assert "Severity: High" in candidates[1].text


@pytest.mark.parametrize(
    "heading",
    [
        "Finding 1: SQLi",
        "Finding #2 XSS",
        "Finding - 3 IDOR",
        "Vulnerability 4 — misconfig",
        "F-01 SQL injection",
        "F12 broken auth",
    ],
)
def test_recognises_common_heading_styles(heading: str) -> None:
    [candidate] = segment_findings(_report(f"{heading}\nbody line\n"))
    assert candidate.heading == heading


def test_no_heading_returns_whole_document() -> None:
    text = "A prose report with no recognisable finding headings at all.\nSecond line."
    [candidate] = segment_findings(_report(text))
    assert candidate.heading == ""
    assert candidate.text == text


def test_prose_starting_with_finding_word_is_not_a_heading() -> None:
    # No digit -> not treated as a finding heading (avoids false positives).
    text = "Findings are summarised below without numbering.\nMore prose."
    [candidate] = segment_findings(_report(text))
    assert candidate.heading == ""


def test_finding_candidate_requires_text() -> None:
    with pytest.raises(ValueError, match="text"):
        FindingCandidate(heading="x", text="")
