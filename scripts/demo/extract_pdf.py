"""Demo for FR-03: extract structured findings from a PDF report with the LLM.

Usage::

    uv run python scripts/demo/extract_pdf.py [report.pdf]

Backend selection is configuration-only (FR-13, ADR-0010): with
``REVALID_LLM_MODEL`` set (e.g. ``ollama:llama3.2`` plus ``OLLAMA_BASE_URL``),
that backend is called for real; otherwise with ``ANTHROPIC_API_KEY`` set, this
calls Claude. With neither, the demo falls back to a deterministic stand-in
model so ``make demo-extract`` always runs offline — it still exercises the
full FR-01 → FR-03 pipeline and the schema gate. Defaults to the synthetic
Juice Shop fixture.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from pydantic_ai.exceptions import ModelAPIError, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.extract import build_extraction_agent, extract_report
from revalid.llm import DEFAULT_MODEL, MODEL_ENV, resolve_model
from revalid.pdf import PdfError, read_pdf

DEFAULT_REPORT = Path(__file__).parents[2] / "tests" / "data" / "juice_shop_report_synthetic.pdf"
_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _offline_extractor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Deterministic stand-in used when no API key is configured."""
    text = ""
    for message in reversed(messages):
        for part in getattr(message, "parts", ()):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                text = part.content
                break
        if text:
            break
    severity = next((s for s in _SEVERITIES if s in text.lower()), "info")
    finding = {
        "title": text.splitlines()[0].strip() if text else "Unknown",
        "severity": severity,
        "description": "Extracted from the report excerpt.",
        "impact": "Attacker-controlled outcome as described.",
        "attack_vector": "As described in the reproduction steps.",
        "affected_endpoints": re.findall(r"/(?:rest|#)[\w/{}?=.#-]*", text),
        "reproduction_steps": [
            line.strip() for line in text.splitlines() if re.match(r"\d+\.\s", line.strip())
        ],
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"response": [finding]})]
    )


def _select_model() -> tuple[Model | KnownModelName | str, str]:
    """Pick the configured backend, else Claude with a key, else the stand-in."""
    if os.environ.get(MODEL_ENV):
        model = resolve_model()
        return model, f"{model} (live, from {MODEL_ENV})"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return DEFAULT_MODEL, f"{DEFAULT_MODEL} (live)"
    return FunctionModel(_offline_extractor), "offline stand-in (no ANTHROPIC_API_KEY)"


def main() -> int:
    """Run the demo: PDF → Markdown → LLM extraction → structured findings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    try:
        report = read_pdf(args.report.read_bytes())
    except PdfError as exc:
        print(f"Rejected the document: {exc}", file=sys.stderr)
        return 1

    model, label = _select_model()
    print(f"Reading {args.report}\nModel: {label}\n")
    try:
        result = extract_report(build_extraction_agent(model), report)
    except (ModelAPIError, UserError) as exc:
        print(f"LLM backend failed ({label}): {exc}", file=sys.stderr)
        return 1

    for index, finding in enumerate(result.findings, start=1):
        print(f"[{index}] {finding.severity.value.upper():8} {finding.title}")
        print(f"      impact: {finding.impact}")
        print(f"      endpoints: {', '.join(finding.affected_endpoints) or '—'}")
        print(f"      steps: {len(finding.reproduction_steps)}  | model: {finding.raw['model']}\n")

    print(f"{len(result.findings)} finding(s) extracted, {len(result.failures)} flagged (gate).")
    if result.failures:
        for failure in result.failures:
            print(f"  flagged: {failure.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
