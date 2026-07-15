"""Model-agnostic LLM backend selection (FR-13, ADR-0010, ADR-0021).

One switch, ``REVALID_LLM_MODEL``, selects the backend for every LLM-using
component: it holds a Pydantic AI model string (``provider:model``, e.g.
``ollama:qwen3.6:27b`` or ``anthropic:claude-sonnet-5``) and defaults to a
local-first Ollama backend (ADR-0021) — no API key or network egress required
out of the box. Switching backends is configuration-only — no code change.
The Ollama backend additionally needs a base URL (``OLLAMA_BASE_URL``, falling
back to :data:`DEFAULT_BASE_URL`) for its OpenAI-compatible endpoint.

The string is resolved to a concrete model lazily, at the first model call
(agents are built with ``defer_model_check=True``), so construction never
needs the network and a misconfigured backend surfaces as a clear error on
first use.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from revalid.domain import Settings

MODEL_ENV = "REVALID_LLM_MODEL"
"""Environment variable that selects the Pydantic AI backend (ADR-0010)."""

DEFAULT_MODEL = "ollama:qwen3.6:27b"
"""Local-first default backend (ADR-0021); used when :data:`MODEL_ENV` is unset.

Not a member of Pydantic AI's ``KnownModelName`` literal (Ollama models are
open-ended, not a fixed catalog), so this is a plain ``str``.
"""

DEFAULT_BASE_URL = "http://localhost:11434/v1"
"""Default OpenAI-compatible endpoint for the local-first Ollama backend (ADR-0021)."""


def resolve_model() -> str:
    """Return the configured Pydantic AI model string.

    Reads :data:`MODEL_ENV` (``REVALID_LLM_MODEL``); an unset or blank value
    falls back to :data:`DEFAULT_MODEL`.

    Returns:
        A ``provider:model`` string for Pydantic AI (validated at first call).
    """
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


def build_model(cfg: Settings) -> Model | str:
    """Construct a concrete Pydantic AI model from a persisted setting (ADR-0021).

    - A ``base_url`` selects an OpenAI-compatible model (Ollama or any
      OpenAI-compatible host); the ``ollama:``/``openai:`` provider prefix is
      stripped from the model name and a placeholder key is used when none is
      stored (Ollama ignores it).
    - Otherwise a native provider with a *stored* key is built explicitly; with
      no stored key the bare ``provider:model`` string is returned so Pydantic AI
      resolves credentials from the environment (backward-compatible with FR-13).

    Args:
        cfg: The persisted settings.

    Returns:
        A Pydantic AI :class:`~pydantic_ai.models.Model` instance, or the model
        string when the environment should supply credentials.
    """
    if cfg.base_url:
        name = (
            cfg.model.split(":", 1)[1]
            if cfg.model.startswith(("ollama:", "openai:"))
            else cfg.model
        )
        return OpenAIChatModel(
            name,
            provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key or "ollama"),
        )
    provider, _, name = cfg.model.partition(":")
    if provider == "anthropic" and cfg.api_key:
        return AnthropicModel(name, provider=AnthropicProvider(api_key=cfg.api_key))
    return cfg.model


def agent_model_name(agent: Agent[Any, Any]) -> str:
    """Return a best-effort stable model identifier for an agent's audit lineage.

    Recording which backend produced a finding or plan is required for the audit
    trail (NFR-02). Works whether the agent was built from a model string or an
    injected model instance (``TestModel``/``FunctionModel`` in tests).

    Args:
        agent: Any Pydantic AI agent.

    Returns:
        The model string, or the instance's ``model_name`` (falling back to its
        ``repr``).
    """
    model = agent.model
    if isinstance(model, str):
        return model
    return getattr(model, "model_name", str(model))
