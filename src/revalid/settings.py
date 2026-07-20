"""Persisted, runtime-editable model/provider setting (FR-13, ADR-0021).

A single ``settings`` row is the source of truth for LLM backend selection. On
a fresh database it is seeded once from the environment (``REVALID_LLM_MODEL`` /
``OLLAMA_BASE_URL``) or the local-first default; thereafter the stored row is
authoritative and the environment no longer overrides it.
"""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from revalid.db import SettingsRecord
from revalid.domain import Settings
from revalid.llm import DEFAULT_BASE_URL, DEFAULT_MODEL

SETTINGS_ID = 1
"""Primary key of the singleton settings row."""

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
"""Anthropic model-list endpoint (authenticated differently from OpenAI's)."""

ANTHROPIC_VERSION = "2023-06-01"
"""Required ``anthropic-version`` header for the Anthropic REST API."""


class ProbeResult(BaseModel):
    """Outcome of a provider connection probe / model discovery (ADR-0021)."""

    reachable: bool
    models: tuple[str, ...] = ()
    error: str | None = None


def _extract_model_ids(payload: object) -> tuple[str, ...]:
    """Pull model ids from an OpenAI-/Anthropic-style ``{"data": [{"id": ...}]}`` body.

    Both providers return the model list under a ``data`` array of objects with
    an ``id``; a non-object or missing ``data`` yields no models (never raises).
    """
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return tuple(str(item["id"]) for item in data if isinstance(item, dict) and "id" in item)


def probe_provider(
    base_url: str | None,
    api_key: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> ProbeResult:
    """Probe an OpenAI-compatible provider and list its models (ADR-0021).

    Hits ``{base_url}/models`` (e.g. Ollama's or OpenAI's OpenAI-compatible
    endpoint). This deliberately bypasses the FR-06 allowlist: the LLM host is
    infrastructure the operator configures, not a pentest target (ADR-0008).

    Args:
        base_url: The provider base URL (must already include any ``/v1`` suffix).
        api_key: Optional bearer token for hosts that require one.
        client: Injectable HTTP client (tests pass a ``MockTransport`` client).

    Returns:
        A :class:`ProbeResult`; ``reachable`` is false with an ``error`` message
        on any failure (no exception escapes).
    """
    if not base_url:
        return ProbeResult(reachable=False, error="set a base URL to discover models")
    owns = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
        return ProbeResult(reachable=True, models=_extract_model_ids(response.json()))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return ProbeResult(reachable=False, error=str(exc))
    finally:
        if owns:
            client.close()


def probe_anthropic(
    api_key: str | None,
    *,
    client: httpx.Client | None = None,
) -> ProbeResult:
    """Discover Claude models from the Anthropic API (ADR-0021).

    Anthropic's model list authenticates with ``x-api-key`` + ``anthropic-version``
    headers rather than OpenAI's bearer scheme, so it needs its own probe. A key
    is required — without one this reports unreachable rather than calling out.

    Args:
        api_key: The Anthropic API key.
        client: Injectable HTTP client (tests pass a ``MockTransport`` client).

    Returns:
        A :class:`ProbeResult`; ``reachable`` is false with an ``error`` message
        on any failure (no exception escapes).
    """
    if not api_key:
        return ProbeResult(reachable=False, error="an Anthropic API key is required")
    owns = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        response = client.get(
            ANTHROPIC_MODELS_URL,
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
        )
        response.raise_for_status()
        return ProbeResult(reachable=True, models=_extract_model_ids(response.json()))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return ProbeResult(reachable=False, error=str(exc))
    finally:
        if owns:
            client.close()


def discover_models(
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
    *,
    client: httpx.Client | None = None,
) -> ProbeResult:
    """Discover a provider's models, dispatching on its authentication scheme.

    ``anthropic`` uses the Anthropic model list (:func:`probe_anthropic`); every
    other provider (``ollama``, ``openai``, or any OpenAI-compatible host) uses
    the OpenAI-compatible ``{base_url}/models`` endpoint (:func:`probe_provider`).

    Args:
        provider: The selected provider id (``ollama`` / ``anthropic`` / ``openai``);
            ``None`` falls back to the OpenAI-compatible probe.
        base_url: Provider base URL (used by the OpenAI-compatible path).
        api_key: Provider API key (required for Anthropic/OpenAI).
        client: Injectable HTTP client (tests pass a ``MockTransport`` client).

    Returns:
        A :class:`ProbeResult` — never raises.
    """
    if provider == "anthropic":
        return probe_anthropic(api_key, client=client)
    return probe_provider(base_url, api_key, client=client)


def _seed_from_env() -> Settings:
    """Compute the initial setting from the environment or the default."""
    model = os.environ.get("REVALID_LLM_MODEL", "").strip() or DEFAULT_MODEL
    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or (
        DEFAULT_BASE_URL if model.startswith("ollama:") else None
    )
    return Settings(model=model, base_url=base_url, api_key=None)


def load_or_seed(session: Session) -> Settings:
    """Return the current setting, seeding the singleton row on first use.

    Args:
        session: An open SQLAlchemy session.

    Returns:
        The persisted :class:`~revalid.domain.Settings`.
    """
    record = session.get(SettingsRecord, SETTINGS_ID)
    if record is None:
        record = SettingsRecord.from_domain(_seed_from_env())
        record.id = SETTINGS_ID
        session.add(record)
        session.commit()
        session.refresh(record)
    return record.to_domain()


def save(
    session: Session,
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    clear_key: bool = False,
) -> Settings:
    """Persist an updated setting and return it.

    The API key is *sticky*: a blank/``None`` ``api_key`` leaves the stored key
    unchanged (so the UI never has to re-enter it); ``clear_key`` explicitly
    removes it.

    Args:
        session: An open SQLAlchemy session.
        model: The Pydantic AI ``provider:model`` string.
        base_url: Provider base URL, or ``None`` for env-configured providers.
        api_key: A new key to store, or blank/``None`` to keep the existing one.
        clear_key: When true, delete the stored key.

    Returns:
        The persisted :class:`~revalid.domain.Settings`.
    """
    record = session.get(SettingsRecord, SETTINGS_ID)
    if record is None:
        record = SettingsRecord.from_domain(_seed_from_env())
        record.id = SETTINGS_ID
        session.add(record)
    record.model = model
    record.base_url = base_url or None
    if clear_key:
        record.api_key = None
    elif api_key:
        record.api_key = api_key
    session.commit()
    session.refresh(record)
    return record.to_domain()
