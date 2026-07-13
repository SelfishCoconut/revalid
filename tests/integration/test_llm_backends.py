"""Integration test for FR-13: config-only LLM backend switching (ADR-0010).

Proves the acceptance criterion at the wiring level: changing only the
environment re-targets the extraction agent to a different provider — Claude
(primary) or local Ollama — with no code change. Each configured string is
resolved to a real provider model object (so the provider wiring, including
the ``openai`` extra Ollama needs, actually exists); no network call is made
(fake credentials, resolution only).
"""

import pytest
from pydantic_ai.models import infer_model

from revalid.extract import build_extraction_agent
from revalid.llm import MODEL_ENV

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("configured", "provider"),
    [
        ("anthropic:claude-sonnet-5", "anthropic"),
        ("ollama:llama3.2", "ollama"),
    ],
)
def test_env_only_switch_retargets_extraction_agent(
    monkeypatch: pytest.MonkeyPatch, configured: str, provider: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv(MODEL_ENV, configured)

    agent = build_extraction_agent()  # identical call for both backends
    assert isinstance(agent.model, str)
    model = infer_model(agent.model)

    assert model.system == provider
