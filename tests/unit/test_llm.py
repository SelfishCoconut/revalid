"""Unit tests for model-agnostic LLM backend selection (FR-13, ADR-0010).

No network: agents are built with a deferred model check, so proving the
config-only switch never needs a backend to exist.
"""

import pytest

from revalid.extract import _model_name, build_extraction_agent
from revalid.llm import DEFAULT_MODEL, MODEL_ENV, resolve_model


def test_defaults_to_claude_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert resolve_model() == DEFAULT_MODEL == "anthropic:claude-sonnet-5"


def test_blank_value_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_ENV, "   ")
    assert resolve_model() == DEFAULT_MODEL


def test_env_var_selects_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_ENV, "ollama:llama3.2")
    assert resolve_model() == "ollama:llama3.2"


def test_agent_follows_config_without_code_change(monkeypatch: pytest.MonkeyPatch) -> None:
    # FR-13 acceptance: switching backends is configuration-only. The same
    # no-argument call builds an Ollama-targeting agent purely from the env.
    monkeypatch.setenv(MODEL_ENV, "ollama:llama3.2")
    agent = build_extraction_agent()
    assert agent.model == "ollama:llama3.2"
    # NFR-02: lineage records the configured backend, whichever it is.
    assert _model_name(agent) == "ollama:llama3.2"
