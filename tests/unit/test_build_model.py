"""Unit tests for constructing a concrete model from a Settings object (ADR-0021)."""

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from revalid.domain import Settings
from revalid.llm import build_model


def test_base_url_builds_openai_compatible_model_stripping_provider_prefix() -> None:
    model = build_model(Settings(model="ollama:qwen3.6:27b", base_url="http://h:11434/v1"))
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "qwen3.6:27b"


def test_anthropic_with_stored_key_builds_native_model() -> None:
    model = build_model(Settings(model="anthropic:claude-sonnet-5", api_key="sk-1"))
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-5"


def test_no_base_url_no_key_falls_back_to_bare_string() -> None:
    model = build_model(Settings(model="anthropic:claude-sonnet-5"))
    assert model == "anthropic:claude-sonnet-5"


def test_bare_model_name_with_base_url_is_used_verbatim() -> None:
    model = build_model(Settings(model="gpt-4o", base_url="http://h/v1"))
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-4o"
