"""System test: verify the live FR-17 egress-locked sandbox (ADR-0025 acceptance).

Requires a running Docker daemon AND the lab (`make lab-up`) AND the sandbox
extra (`uv sync --extra sandbox`). Skips gracefully when Docker OR the lab is
missing — so a developer without the full setup still gets a green suite; CI
(system-tests.yml) provisions both. The acceptance: a real ``DockerSandbox``
can reach the lab target but NOT the public internet (the egress
lock, FR-17 AC4 / NFR-03).
"""

from __future__ import annotations

import time

import httpx
import pytest
from tests._retest_helpers import egress_probe_command

from revalid.sandbox import (
    DockerSandbox,
    SandboxUnavailableError,
    lab_base_url,
)

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


#: Tools the retest agent is entitled to assume are present (issue #105). Each
#: must answer `-h`/`--version` *offline*, since the sandbox has no egress.
EXPECTED_TOOLS = ("curl", "nmap", "sqlmap", "nikto", "hydra", "jq", "nc", "python3")


def test_sandbox_image_carries_the_pentest_toolbox() -> None:
    """The sandbox ships the tools the agent may propose commands for (#105).

    Worth asserting rather than trusting the Dockerfile: the container cannot
    install anything at runtime (it is egress-locked), so a tool missing from the
    image is a command that fails at retest time — and an agent that concludes
    from a failed probe is exactly the confidently-wrong verdict NFR-01 forbids.
    """
    if not _docker_available():
        pytest.skip("docker not available; run with the sandbox extra + a running daemon")

    box = DockerSandbox(session_id=9998)
    try:
        box.start()
        missing = [
            tool
            for tool in EXPECTED_TOOLS
            if box.exec(f"command -v {tool}", timeout=15).exit_code != 0
        ]
        assert not missing, f"sandbox image is missing: {', '.join(missing)}"
    finally:
        box.stop()


def test_missing_sandbox_image_says_how_to_build_it() -> None:
    """An unbuilt image fails with the fix, not a raw Docker error (#105)."""
    if not _docker_available():
        pytest.skip("docker not available; run with the sandbox extra + a running daemon")

    box = DockerSandbox(session_id=9997, image="revalid-sandbox:definitely-not-built")
    with pytest.raises(SandboxUnavailableError, match="make sandbox-image"):
        box.start()


def _internet_available() -> bool:
    """Return whether the test host itself has internet (the online test needs it)."""
    try:
        return httpx.get("https://example.com", timeout=8).status_code < 500
    except httpx.RequestError:
        return False


def test_online_sandbox_reaches_its_scope_and_nothing_else() -> None:
    """The L3 egress gateway (ADR-0045): scoped host reachable, off-scope blocked, tamper-proof.

    This is the path that shipped non-functional twice (missing ``iptables`` in
    the image, then nmap's file capabilities colliding with the shrunk bounding
    set) — both invisible to unit tests, so it earns a live acceptance. Scopes to
    ``example.com`` (an IANA-reserved, always-up host) and asserts:

    - the scoped host is reachable **by every tool** — curl *and* nmap, the whole
      point of moving off the HTTP-only proxy;
    - a *different* host is blocked (the allowlist is closed, not open);
    - the sandbox cannot alter its own egress (``iptables -F`` is denied — it
      holds ``NET_RAW`` but not ``NET_ADMIN``).

    Needs a Docker daemon, the sandbox extra, and host internet; skips otherwise.
    """
    if not _docker_available():
        pytest.skip("docker not available; run with the sandbox extra + a running daemon")
    if not _internet_available():
        pytest.skip("no host internet; the online egress test needs a route out")

    box = DockerSandbox(session_id=9998)
    try:
        box.start(("example.com",))
        scoped = box.exec(
            "curl --max-time 20 -s -o /dev/null -w '%{http_code}' https://example.com/", timeout=30
        )
        assert scoped.stdout.strip() == "200"  # the scoped host is reachable

        nmap = box.exec("nmap -Pn -sT -p443 example.com", timeout=60)
        assert nmap.exit_code == 0 and "open" in nmap.stdout  # every tool works, not just HTTP

        off = box.exec(
            "curl --max-time 12 -s -o /dev/null -w '%{http_code}' https://cloudflare.com/",
            timeout=25,
        )
        assert off.exit_code != 0  # a different host is blocked by the allowlist

        tamper = box.exec("iptables -F 2>&1; echo rc=$?", timeout=15)
        assert "rc=0" not in tamper.stdout  # the model cannot change egress (no NET_ADMIN)
    finally:
        box.stop()
