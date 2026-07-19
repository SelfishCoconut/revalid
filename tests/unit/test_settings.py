"""Unit tests for the persisted model/provider setting (FR-13, ADR-0021)."""

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from revalid import settings as settings_mod
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.llm import DEFAULT_BASE_URL, DEFAULT_MODEL


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as s:
        yield s


def test_seed_on_empty_db_uses_local_first_default(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REVALID_LLM_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    cfg = settings_mod.load_or_seed(session)
    assert cfg.model == DEFAULT_MODEL == "ollama:qwen3.5:9b"
    assert cfg.base_url == DEFAULT_BASE_URL == "http://localhost:11434/v1"
    assert cfg.api_key is None


def test_seed_reads_env_when_present(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVALID_LLM_MODEL", "ollama:llama3.2")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host:1234/v1")
    cfg = settings_mod.load_or_seed(session)
    assert cfg.model == "ollama:llama3.2"
    assert cfg.base_url == "http://host:1234/v1"


def test_load_is_idempotent_and_persists_once(session: Session) -> None:
    first = settings_mod.load_or_seed(session)
    second = settings_mod.load_or_seed(session)
    assert first == second


def test_save_updates_model_and_base_url(session: Session) -> None:
    settings_mod.load_or_seed(session)
    cfg = settings_mod.save(
        session, model="anthropic:claude-sonnet-5", base_url=None, api_key="sk-123"
    )
    assert cfg.model == "anthropic:claude-sonnet-5"
    assert cfg.base_url is None
    assert cfg.api_key == "sk-123"


def test_save_with_blank_key_keeps_existing_key(session: Session) -> None:
    settings_mod.save(session, model="anthropic:claude-sonnet-5", base_url=None, api_key="sk-123")
    cfg = settings_mod.save(session, model="anthropic:claude-sonnet-5", base_url=None, api_key="")
    assert cfg.api_key == "sk-123"


def test_save_clear_key_removes_it(session: Session) -> None:
    settings_mod.save(session, model="x:y", base_url=None, api_key="sk-123")
    cfg = settings_mod.save(session, model="x:y", base_url=None, api_key=None, clear_key=True)
    assert cfg.api_key is None


def test_default_max_steps_seeds_to_8_and_roundtrips(session: Session) -> None:
    """The retest step budget seeds to 8 and persists any value, including no-limit (Slice 9)."""
    assert settings_mod.load_or_seed(session).default_max_steps == 8
    assert (
        settings_mod.save(session, model="x:y", base_url=None, api_key=None, default_max_steps=20)
    ).default_max_steps == 20
    # None = no limit.
    assert (
        settings_mod.save(session, model="x:y", base_url=None, api_key=None, default_max_steps=None)
    ).default_max_steps is None
