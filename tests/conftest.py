"""Shared fixtures and deterministic LLM stand-ins for the test suite.

Keeps the FR-03 extraction stand-in in one place so every test that ingests the
committed fixture report drives the same offline ``FunctionModel`` (no network).
"""

import re
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.extract import ExtractedFinding, build_extraction_agent

_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _candidate_text(messages: list[ModelMessage]) -> str:
    """Return the last user-prompt text (one report candidate) from the messages."""
    for message in reversed(messages):
        for part in getattr(message, "parts", ()):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def _fake_extractor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Deterministically turn one report candidate into one complete finding."""
    text = _candidate_text(messages)
    severity = next((s for s in _SEVERITIES if s in text.lower()), "info")
    finding: dict[str, Any] = {
        "title": text.splitlines()[0].strip() if text.strip() else "Untitled",
        "severity": severity,
        "description": "Extracted from the report excerpt.",
        "impact": "Attacker-controlled outcome.",
        "attack_vector": "As described.",
        "affected_endpoints": re.findall(r"/(?:rest|#)[\w/{}?=.#-]*", text),
        "reproduction_steps": [
            line.strip() for line in text.splitlines() if re.match(r"\d+\.\s", line.strip())
        ],
    }
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"response": [finding]})]
    )


@pytest.fixture
def extraction_agent() -> Agent[None, list[ExtractedFinding]]:
    """A finding-extraction agent backed by a deterministic FunctionModel (no network)."""
    return build_extraction_agent(FunctionModel(_fake_extractor))
