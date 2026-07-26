"""Deterministic FR-03 extraction stand-in shared across the test tiers.

The real extractor sends a whole report to the model in one call and gets back a
list of findings (ADR-0047). This ``FunctionModel`` callable mimics that
deterministically and offline: it splits the report Markdown into finding
sections by their headings and returns one complete, schema-valid finding per
section — so every tier drives the same reproducible stand-in with no network.

Importable as ``tests._extract_helpers`` (the repo root is on ``sys.path`` and
``tests`` is a package).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo

_SEVERITIES = ("critical", "high", "medium", "low", "info")

# A Markdown heading line that names a finding ("## **Finding 1 — ...**"). The
# stand-in uses it only to segment the fixture for a realistic multi-finding
# answer; the production extractor does no such segmentation (ADR-0047).
_FINDING_HEADING = re.compile(r"^#{1,6}\s+.*\bFinding\b.*$", re.IGNORECASE | re.MULTILINE)
_ENDPOINT = re.compile(r"/(?:rest|#)[\w/{}?=.#-]*")
_STEP = re.compile(r"\d+\.\s")


def _report_text(messages: list[ModelMessage]) -> str:
    """Return the last user-prompt text (the whole report) from the messages."""
    for message in reversed(messages):
        for part in getattr(message, "parts", ()):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def _title(heading_line: str) -> str:
    """Strip Markdown heading/emphasis markup down to the bare finding title."""
    return heading_line.lstrip("#").strip().strip("*").strip()


def _finding(title: str, body: str) -> dict[str, Any]:
    """Build one complete, schema-valid finding dict from a report section."""
    return {
        "title": title or "Untitled",
        "severity": next((s for s in _SEVERITIES if s in body.lower()), "info"),
        "description": "Extracted from the report excerpt.",
        "impact": "Attacker-controlled outcome as described.",
        "attack_vector": "As described in the reproduction steps.",
        "affected_endpoints": _ENDPOINT.findall(body),
        "reproduction_steps": [
            line.strip() for line in body.splitlines() if _STEP.match(line.strip())
        ],
    }


def findings_from_report(text: str) -> list[dict[str, Any]]:
    """Split a whole-report Markdown string into one finding dict per heading."""
    matches = list(_FINDING_HEADING.finditer(text))
    if not matches:  # no finding headings — treat the whole document as one finding
        first_line = text.splitlines()[0] if text.strip() else ""
        return [_finding(_title(first_line), text)]
    bounds = [m.start() for m in matches] + [len(text)]
    findings = []
    for index, match in enumerate(matches):
        section = text[match.start() : bounds[index + 1]]
        findings.append(_finding(_title(section.splitlines()[0]), section))
    return findings


def fake_extractor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Turn a whole report into its list of findings in one call (one per heading)."""
    findings = findings_from_report(_report_text(messages))
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"response": findings})]
    )
