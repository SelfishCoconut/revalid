"""System test: FR-13 — the extraction pipeline runs against a live local Ollama.

Requires a running Ollama server exposed via ``OLLAMA_BASE_URL`` (OpenAI-compat
endpoint, e.g. ``http://localhost:11434/v1``) with the model pulled (defaults
to ``llama3.2``; override with ``REVALID_LLM_MODEL=ollama:<model>``). Skips
gracefully when no server is reachable so the suite stays green without one.

This is the live half of the FR-13 acceptance evidence: the *same* extraction
suite (FR-01 fixture → FR-03 per-candidate agent → schema gate) executes on the
fallback backend purely through configuration.
"""

import os
from pathlib import Path

import httpx
import pytest

from revalid.extract import build_extraction_agent, extract_report
from revalid.llm import MODEL_ENV, resolve_model
from revalid.pdf import read_pdf

FIXTURE = Path(__file__).parents[1] / "data" / "juice_shop_report_synthetic.pdf"


def _ollama_ready(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/models", timeout=5).status_code == 200
    except httpx.RequestError:
        return False


@pytest.mark.system
def test_extraction_suite_runs_on_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    base_url = os.environ.get("OLLAMA_BASE_URL", "")
    if not base_url:
        pytest.skip("OLLAMA_BASE_URL not set; start Ollama and export it")
    if not _ollama_ready(base_url):
        pytest.skip(f"Ollama not reachable at {base_url}")
    if not resolve_model().startswith("ollama:"):
        monkeypatch.setenv(MODEL_ENV, "ollama:llama3.2")

    agent = build_extraction_agent()  # backend comes from the environment only
    result = extract_report(agent, read_pdf(FIXTURE.read_bytes()))

    # The run completed through the schema gate: every candidate ended up as a
    # schema-valid finding or a flagged failure — nothing silently accepted.
    assert result.findings or result.failures
    for finding in result.findings:
        assert str(finding.raw["model"]).startswith("ollama")
