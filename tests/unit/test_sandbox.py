"""Unit tests for the FR-17 egress-locked retest sandbox (ADR-0025, Slice 0).

Only the pure surface is exercised here: ``FakeSandbox`` (a scripted in-memory
double, no Docker), and the pure helpers. The live ``DockerSandbox`` needs the
optional ``sandbox`` extra and a Docker daemon — it is covered by the nightly
system test.
"""

from __future__ import annotations

import base64
import re
from types import SimpleNamespace
from typing import Any

import pytest

from revalid.sandbox import (
    EGRESS_PROXY_PORT,
    CommandResult,
    DockerSandbox,
    FakeSandbox,
    SandboxUnavailableError,
    egress_probe_command,
    egress_proxy_name,
    internal_network_name,
    is_lab_scope,
    lab_host,
    online_scope_hosts,
    squid_allowlist_config,
)


def test_fake_sandbox_replays_scripted_results_and_records_commands() -> None:
    box = FakeSandbox([CommandResult(stdout="hi", stderr="", exit_code=0, elapsed_ms=3)])
    box.start()
    result = box.exec("echo hi", timeout=5.0)
    assert result.stdout == "hi"
    assert box.commands == ["echo hi"]
    box.stop()


def test_fake_sandbox_callable_script() -> None:
    box = FakeSandbox(lambda cmd: CommandResult(stdout=cmd, stderr="", exit_code=0, elapsed_ms=1))
    assert box.exec("whoami", timeout=1.0).stdout == "whoami"


def test_fake_sandbox_exhausted_raises() -> None:
    box = FakeSandbox([])
    with pytest.raises(SandboxUnavailableError):
        box.exec("echo hi", timeout=1.0)


def test_internal_network_name_is_session_scoped() -> None:
    assert internal_network_name(7) == "revalid-retest-7"


def test_egress_probe_command_targets_a_host() -> None:
    host = "example.com"
    cmd = egress_probe_command(host)
    # Assert the whole command contract (bounded timeout, silent, body discarded,
    # https to the host) rather than a bare-hostname substring check — the latter
    # trips CodeQL's incomplete-url-substring-sanitization heuristic and is a
    # weaker test anyway. The host is interpolated, so no host string literal is
    # compared.
    assert cmd == f"curl --max-time 5 --silent --show-error --output /dev/null https://{host}"


# --- scope-based provisioning (ADR-0041, issue #208) ---


def test_lab_host_is_the_lab_base_url_host() -> None:
    # Default lab base url is http://localhost:3000.
    assert lab_host() == "localhost:3000"


def test_is_lab_scope_for_empty_and_lab_only() -> None:
    assert is_lab_scope(()) is True  # no scope defaults to the lab
    assert is_lab_scope(("localhost:3000",)) is True


def test_is_lab_scope_false_for_an_online_host() -> None:
    assert is_lab_scope(("domain.com",)) is False
    assert is_lab_scope(("localhost:3000", "domain.com")) is False  # any online host -> online


def test_online_scope_hosts_drops_the_lab_host() -> None:
    assert online_scope_hosts(("localhost:3000", "domain.com", "api.x.com")) == (
        "domain.com",
        "api.x.com",
    )


def test_fake_sandbox_records_the_scope_it_was_started_with() -> None:
    box = FakeSandbox([])
    box.start(("domain.com",))
    assert box.scope_hosts == ("domain.com",)


def test_egress_proxy_name_is_session_scoped() -> None:
    assert egress_proxy_name(7) == "revalid-retest-proxy-7"


def test_squid_allowlist_denies_all_but_the_scoped_domains() -> None:
    conf = squid_allowlist_config(("domain.com", "api.example.com:8443"))
    assert f"http_port {EGRESS_PROXY_PORT}" in conf
    # Ports are dropped for the dstdomain match; both hosts are allowlisted.
    assert "acl scoped dstdomain api.example.com domain.com" in conf
    assert "http_access allow scoped" in conf
    # The closed default — anything not scoped is denied (no open relay).
    assert "http_access deny all" in conf


def test_proxy_launch_writes_a_real_multi_line_squid_config() -> None:
    """The launch one-liner must reconstruct the config *with real newlines*.

    Regression (issue #226): the original built the command as
    ``printf '%s' {config!r}``. Python's ``repr`` escapes newlines to the two
    characters ``\\n`` and ``printf '%s'`` does not interpret escapes, so Squid
    got a single-line file full of literal ``\\n``, died on it, and every
    online-scope retest silently had no route to its target. Asserting on the
    *decoded payload* pins the property that actually matters — what lands in
    ``squid.conf`` — rather than the shape of the shell string.
    """
    box = DockerSandbox(session_id=3)
    launch = box._proxy_launch(("www.hackthissite.org",))

    encoded = launch.split("echo ", 1)[1].split(" |", 1)[0]
    written = base64.b64decode(encoded).decode()

    assert written == squid_allowlist_config(("www.hackthissite.org",))
    assert written.splitlines()[0] == f"http_port {EGRESS_PROXY_PORT}"
    assert len(written.splitlines()) == 5
    # The exact defect: no literal backslash-n may survive into the config.
    assert "\\n" not in written
    # base64 payloads are shell-safe, so no quoting rule can mangle the config.
    assert re.fullmatch(r"[A-Za-z0-9+/=]+", encoded)


class _FakeContainer:
    """Minimal stand-in for a docker-py container."""

    def __init__(self, ip: str = "172.30.0.2") -> None:
        self.attrs = {"NetworkSettings": {"Networks": {"revalid-retest-5": {"IPAddress": ip}}}}

    def reload(self) -> None:
        """No-op: the fake's attrs are already populated."""


class _FakeNetworks:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create(self, name: str, **kwargs: object) -> object:
        self.created.append({"name": name, **kwargs})
        return object()

    def get(self, _name: str) -> Any:
        return SimpleNamespace(connect=lambda _c: None)


class _FakeContainers:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def run(self, image: str, **kwargs: Any) -> _FakeContainer:
        self.runs.append({"image": image, **kwargs})
        return _FakeContainer()


class _FakeDockerClient:
    def __init__(self) -> None:
        self.networks = _FakeNetworks()
        self.containers = _FakeContainers()


def test_online_proxy_overrides_the_image_entrypoint() -> None:
    """The proxy shell must be passed as ``entrypoint``, never as ``command``.

    Regression (issue #226): the Squid images declare
    ``ENTRYPOINT ["entrypoint.sh"]`` with ``CMD ["-f", …, "-NYC"]``, so a
    ``command=["sh", "-c", …]`` was handed to Squid as *its own* arguments —
    the container died with "'-c': unrecognized option", the proxy never
    listened, and the sandbox had no route out. Only overriding the entrypoint
    actually gives our shell control.

    Needs the optional ``sandbox`` extra: ``_start_online`` imports
    ``docker.errors`` for its fail-closed except clause. The Docker *daemon* is
    not needed — the client is a fake — so this runs anywhere the package is
    installed, which is why CI's unit job syncs the extra.
    """
    pytest.importorskip("docker")
    box = DockerSandbox(session_id=5)
    client = _FakeDockerClient()

    box._start_online(client, ("www.hackthissite.org",))

    proxy_run = client.containers.runs[0]
    assert proxy_run["entrypoint"][:2] == ["sh", "-c"]
    assert "squid" in proxy_run["entrypoint"][2]
    assert "command" not in proxy_run

    # The session network stays internal: the proxy is the only way out.
    assert client.networks.created[0]["internal"] is True

    # The sandbox is pointed at the proxy for both schemes, upper and lower case
    # (tools read one or the other).
    env = client.containers.runs[1]["environment"]
    expected = f"http://172.30.0.2:{EGRESS_PROXY_PORT}"
    assert {env["http_proxy"], env["https_proxy"], env["HTTP_PROXY"], env["HTTPS_PROXY"]} == {
        expected
    }
