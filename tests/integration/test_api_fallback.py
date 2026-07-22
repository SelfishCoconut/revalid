"""Integration test: an unknown ``/api`` path 404s for every method (#157).

Regression guard for a real trap. The SPA catch-all (``_mount_spa``) registers
``@app.get("/{full_path:path}")`` so deep links reload into the SPA — which made
it the *only* route matching an unknown ``/api`` path. A non-GET request to one
therefore matched the path but not the method, and Starlette answered
``405 Method Not Allowed``: every renamed, mistyped or not-yet-deployed endpoint
surfaced in the console as a baffling "Method Not Allowed" instead of "no such
endpoint". ``_register_api_fallback`` claims those paths for all methods.
"""

import pytest
from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import IN_MEMORY, create_db_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(engine=create_db_engine(IN_MEMORY)))


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_unknown_api_path_is_404_for_every_method(client: TestClient, method: str) -> None:
    response = getattr(client, method)("/api/no-such-endpoint")

    assert response.status_code == 404
    assert "no-such-endpoint" in response.json()["detail"]


def test_known_route_still_wins_over_the_fallback(client: TestClient) -> None:
    # The fallback is registered after the real routes, so it must never shadow
    # one — and a *wrong method* on a real path stays a 405, which is accurate.
    assert client.get("/api/health").status_code == 200
    assert client.delete("/api/health").status_code == 405
