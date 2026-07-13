"""Generate the synthetic Juice Shop PDF fixture for FR-01 tests.

The committed ``tests/data/juice_shop_report_synthetic.pdf`` stands in for
Álvaro's scrubbed real report until he drops it in (ADR-0007). This script
regenerates it deterministically so the binary can be reproduced from source and
reviewed. reportlab is a **dev-only** dependency — tests read the committed PDF
and never import this module.

Usage::

    uv run python scripts/gen_fixture_pdf.py

The findings mimic well-known OWASP Juice Shop vulnerabilities. Every value is
synthetic lab data (no real client/engagement content). Finding 1 renders its
metadata as a table so the fixture exercises FR-01's table tolerance.
"""

from __future__ import annotations

from pathlib import Path

import reportlab.rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Reproducible output: fixed producer string and timestamps, so re-running
# yields a byte-identical PDF and doesn't create spurious diffs.
reportlab.rl_config.invariant = 1

OUTPUT = Path(__file__).parents[1] / "tests" / "data" / "juice_shop_report_synthetic.pdf"


def _finding_one() -> list[Flowable]:
    """SQL injection, with metadata rendered as a table (table tolerance test)."""
    styles = getSampleStyleSheet()
    table = Table(
        [
            ["Attribute", "Value"],
            ["Severity", "Critical"],
            ["CWE", "CWE-89"],
            ["CVSS", "9.8"],
            ["Affected endpoint", "POST /rest/user/login"],
        ],
        colWidths=[140, 300],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return [
        Paragraph("Finding 1 — SQL Injection in Login Form", styles["Heading2"]),
        Spacer(1, 6),
        table,
        Spacer(1, 8),
        Paragraph(
            "Description: The login endpoint concatenates the submitted email directly "
            "into a SQL query. A crafted email bypasses authentication and returns a "
            "valid session for the first matching account.",
            styles["BodyText"],
        ),
        Paragraph("Steps to reproduce:", styles["BodyText"]),
        Paragraph("1. Browse to the login page at /#/login.", styles["BodyText"]),
        Paragraph("2. Enter the email <b>' OR 1=1--</b> and any password.", styles["BodyText"]),
        Paragraph(
            "3. Observe a successful login as the administrator account.", styles["BodyText"]
        ),
    ]


def _finding_two() -> list[Flowable]:
    """Reflected XSS."""
    styles = getSampleStyleSheet()
    return [
        Paragraph("Finding 2 — Reflected Cross-Site Scripting in Search", styles["Heading2"]),
        Paragraph("Severity: High", styles["BodyText"]),
        Paragraph("Affected endpoint: GET /#/search?q=", styles["BodyText"]),
        Paragraph(
            "Description: The product search reflects the q parameter into the page "
            "without encoding, executing attacker-supplied markup in the victim's browser.",
            styles["BodyText"],
        ),
        Paragraph("Steps to reproduce:", styles["BodyText"]),
        Paragraph(
            "1. Submit the search term &lt;iframe src=javascript:alert(1)&gt;.",
            styles["BodyText"],
        ),
        Paragraph(
            "2. Observe the injected iframe executing on the results page.", styles["BodyText"]
        ),
    ]


def _finding_three() -> list[Flowable]:
    """Broken access control / IDOR."""
    styles = getSampleStyleSheet()
    return [
        Paragraph("Finding 3 — Broken Access Control on Basket", styles["Heading2"]),
        Paragraph("Severity: High", styles["BodyText"]),
        Paragraph("Affected endpoint: GET /rest/basket/{id}", styles["BodyText"]),
        Paragraph(
            "Description: Basket identifiers are sequential and not tied to the "
            "authenticated user, so any basket can be read by changing the id.",
            styles["BodyText"],
        ),
        Paragraph("Steps to reproduce:", styles["BodyText"]),
        Paragraph("1. Log in and note your own basket id at /rest/basket/1.", styles["BodyText"]),
        Paragraph("2. Request /rest/basket/2 and read another user's basket.", styles["BodyText"]),
    ]


def _finding_four() -> list[Flowable]:
    """Security misconfiguration."""
    styles = getSampleStyleSheet()
    return [
        Paragraph("Finding 4 — Verbose Error Messages Expose Stack Traces", styles["Heading2"]),
        Paragraph("Severity: Medium", styles["BodyText"]),
        Paragraph("Affected endpoint: GET /rest/products/search", styles["BodyText"]),
        Paragraph(
            "Description: Malformed requests return an unhandled exception including the "
            "SQL statement and server file paths, aiding further attacks.",
            styles["BodyText"],
        ),
        Paragraph("Steps to reproduce:", styles["BodyText"]),
        Paragraph("1. Send GET /rest/products/search?q=') to the API.", styles["BodyText"]),
        Paragraph("2. Observe the full stack trace in the JSON response.", styles["BodyText"]),
    ]


def build() -> Path:
    """Render the synthetic report to :data:`OUTPUT` and return its path."""
    styles = getSampleStyleSheet()
    story: list[Flowable] = [
        Paragraph("OWASP Juice Shop — Penetration Test Report", styles["Title"]),
        Paragraph("Synthetic evaluation fixture (lab data only)", styles["Italic"]),
        Spacer(1, 18),
        *_finding_one(),
        *_finding_two(),
        PageBreak(),
        *_finding_three(),
        *_finding_four(),
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        title="OWASP Juice Shop - Penetration Test Report (synthetic)",
        author="revalid test fixtures",
    )
    doc.build(story)
    return OUTPUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
