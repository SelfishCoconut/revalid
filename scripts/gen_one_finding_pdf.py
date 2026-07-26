"""Generate the minimal one-finding PDF fixture for local-backend extraction.

Whole-document extraction (ADR-0047) hands the entire report to the model in one
call, so a full multi-finding report needs a large context window — trivial on a
hosted model, out of reach for a small local one. This fixture is the deliberately
tiny case that a local Ollama model *can* ingest end to end, so FR-03 has a
runnable local-extraction demonstration (``tests/system/test_ollama_extraction.py``).

reportlab is a **dev-only** dependency — tests read the committed PDF and never
import this module.

Usage::

    uv run python scripts/gen_one_finding_pdf.py
"""

from __future__ import annotations

from pathlib import Path

import reportlab.rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# Reproducible output: fixed producer string and timestamps, so re-running yields
# a byte-identical PDF and doesn't create spurious diffs.
reportlab.rl_config.invariant = 1

OUTPUT = Path(__file__).parents[1] / "tests" / "data" / "one_finding_report.pdf"

_LINES = [
    ("OWASP Juice Shop — Mini Pentest Report", "Title"),
    ("Finding 1 — SQL Injection in Login Form", "Heading2"),
    ("Severity: Critical", "Normal"),
    ("Affected endpoint: POST /rest/user/login", "Normal"),
    (
        "Description: The login endpoint concatenates the submitted email directly into a "
        "SQL query, so a crafted email bypasses authentication.",
        "Normal",
    ),
    ("Impact: An attacker logs in as any user without credentials.", "Normal"),
    ("Steps to reproduce:", "Normal"),
    ("1. Browse to /#/login.", "Normal"),
    ("2. Submit the email ' OR 1=1-- with any password.", "Normal"),
    ("3. Observe that an authenticated session is returned.", "Normal"),
]


def build() -> None:
    """Render the one-finding report to :data:`OUTPUT` deterministically."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4)
    flow = []
    for text, style in _LINES:
        flow.append(Paragraph(text, styles[style]))
        if style == "Title":
            flow.append(Spacer(1, 12))
    doc.build(flow)


if __name__ == "__main__":
    build()
    print(f"wrote {OUTPUT}")
