"""Model-agnostic LLM backend selection (FR-13, ADR-0010).

One switch, ``REVALID_LLM_MODEL``, selects the backend for every LLM-using
component: it holds a Pydantic AI model string (``provider:model``, e.g.
``anthropic:claude-sonnet-5`` or ``ollama:llama3.2``) and defaults to Claude
(ADR-0002). Switching backends is configuration-only — no code change. The
Ollama backend additionally needs the provider's own ``OLLAMA_BASE_URL``
variable; there is deliberately no default server address (ADR-0010).

The string is resolved to a concrete model lazily, at the first model call
(agents are built with ``defer_model_check=True``), so construction never
needs the network and a misconfigured backend surfaces as a clear error on
first use.
"""

from __future__ import annotations

import os

from pydantic_ai.models import KnownModelName

MODEL_ENV = "REVALID_LLM_MODEL"
"""Environment variable that selects the Pydantic AI backend (ADR-0010)."""

DEFAULT_MODEL: KnownModelName = "anthropic:claude-sonnet-5"
"""Claude is the primary backend (ADR-0002); used when :data:`MODEL_ENV` is unset."""


def resolve_model() -> str:
    """Return the configured Pydantic AI model string.

    Reads :data:`MODEL_ENV` (``REVALID_LLM_MODEL``); an unset or blank value
    falls back to :data:`DEFAULT_MODEL`.

    Returns:
        A ``provider:model`` string for Pydantic AI (validated at first call).
    """
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
