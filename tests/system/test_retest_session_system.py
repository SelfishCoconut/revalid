"""System test: verify the live FR-17 egress-locked sandbox (ADR-0025 acceptance).

Requires a running Docker daemon AND the lab (`make lab-up`) AND the sandbox
extra (`uv sync --extra sandbox`). Skips gracefully when Docker OR the lab is
missing — mirroring the ``test_browser_xss_system.py`` precedent — so a
developer without the full setup still gets a green suite; CI
(system-tests.yml) provisions both. The acceptance: a real ``DockerSandbox``
can reach the allowlisted lab target but NOT the public internet (the egress
lock, FR-17 AC4 / NFR-03).
"""

from __future__ import annotations

import time

import httpx
import pytest

from revalid.retest import lab_base_url
from revalid.sandbox import DockerSandbox, egress_probe_command

pytestmark = pytest.mark.system


def _docker_available() -> bool:
    """Return whether a Docker daemon is reachable (best-effort ping)."""
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


def _wait_for_lab(base_url: str, timeout_s: float = 60.0) -> bool:
    """Return whether the lab's version endpoint answers within ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    version_url = f"{base_url}/rest/admin/application-version"
    while time.monotonic() < deadline:
        try:
            if httpx.get(version_url, timeout=5).status_code == 200:
                return True
        except httpx.RequestError:
            pass
        time.sleep(2)
    return False


def test_sandbox_can_reach_lab_but_not_the_internet() -> None:
    """A live sandbox reaches the lab container but is egress-locked from the internet."""
    if not _docker_available():
        pytest.skip("docker not available; run with the sandbox extra + a running daemon")
    base_url = lab_base_url()
    if not _wait_for_lab(base_url):
        pytest.skip(f"lab not reachable at {base_url}; run `make lab-up`")

    box = DockerSandbox(session_id=9999)
    try:
        box.start()
        lab = box.exec(
            "curl --max-time 5 -s -o /dev/null -w '%{http_code}' "
            "http://revalid-juice-shop:3000/rest/admin/application-version",
            timeout=15,
        )
        assert lab.exit_code == 0  # lab is a network member -> reachable

        egress = box.exec(egress_probe_command("example.com"), timeout=15)
        assert egress.exit_code != 0  # internet is NOT reachable (egress lock, NFR-03)
    finally:
        box.stop()
