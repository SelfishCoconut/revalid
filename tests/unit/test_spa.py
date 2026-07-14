"""Unit tests for FR-11 SPA serving: static files + client-routing catch-all."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from revalid.app import _mount_spa


def _spa_client(dist: Path) -> TestClient:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>revalid</title>")
    (dist / "app.js").write_text("console.log(1)")
    app = FastAPI()
    _mount_spa(app, dist=dist)
    return TestClient(app)


def test_serves_index_for_root_and_deep_links(tmp_path: Path) -> None:
    client = _spa_client(tmp_path / "dist")
    root = client.get("/")
    assert root.status_code == 200
    assert "revalid" in root.text
    # A client-side deep link reloads into index.html (BrowserRouter support).
    deep = client.get("/findings/5")
    assert deep.status_code == 200
    assert "revalid" in deep.text


def test_serves_real_built_files(tmp_path: Path) -> None:
    asset = _spa_client(tmp_path / "dist").get("/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text


def test_catch_all_never_shadows_api(tmp_path: Path) -> None:
    # An unmatched /api/* path 404s instead of returning index.html, so a real
    # /api router registered before the catch-all always wins.
    assert _spa_client(tmp_path / "dist").get("/api/whatever").status_code == 404


def test_mount_is_noop_without_a_build(tmp_path: Path) -> None:
    app = FastAPI()
    _mount_spa(app, dist=tmp_path / "absent")
    # No catch-all registered: an unknown path 404s normally.
    assert TestClient(app).get("/").status_code == 404
