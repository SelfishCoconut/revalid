"""Settings CRUD API — masked key, sticky key, runtime update (ADR-0021)."""

import pytest
from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import create_db_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(engine=create_db_engine(":memory:")))


def test_get_returns_seeded_default_with_no_key(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["model"] == "ollama:qwen3.6:27b"
    assert body["base_url"] == "http://localhost:11434/v1"
    assert body["api_key_set"] is False
    assert body["api_key_hint"] is None
    assert "api_key" not in body


def test_put_updates_model_and_masks_stored_key(client: TestClient) -> None:
    resp = client.put(
        "/api/settings",
        json={"model": "anthropic:claude-sonnet-5", "base_url": None, "api_key": "sk-secret99"},
    )
    body = resp.json()
    assert body["model"] == "anthropic:claude-sonnet-5"
    assert body["api_key_set"] is True
    assert body["api_key_hint"] == "et99"
    assert "api_key" not in body
    # Sticky: a follow-up PUT without a key keeps it set.
    again = client.put(
        "/api/settings", json={"model": "anthropic:claude-sonnet-5", "base_url": None}
    ).json()
    assert again["api_key_set"] is True


def test_put_rejects_empty_model(client: TestClient) -> None:
    assert client.put("/api/settings", json={"model": "", "base_url": None}).status_code == 422
