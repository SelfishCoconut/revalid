"""Unit tests for the FR-17 egress-locked retest sandbox (ADR-0025, Slice 0).

Only the pure surface is exercised here: ``FakeSandbox`` (a scripted in-memory
double, no Docker), and the pure helpers. The live ``DockerSandbox`` needs the
optional ``sandbox`` extra and a Docker daemon — it is covered by the nightly
system test.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests._retest_helpers import egress_probe_command

from revalid.sandbox import (
    CommandResult,
    DockerSandbox,
    FakeSandbox,
    SandboxUnavailableError,
    _teardown_by_name,
    dns_resolver,
    egress_firewall_script,
    gateway_container_name,
    internal_network_name,
    is_lab_scope,
    lab_host,
    online_scope_hosts,
    resolve_scope_ips,
    sandbox_container_name,
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


# --- Online L3 egress gateway (ADR-0045) ---------------------------------


def test_gateway_and_sandbox_container_names_are_session_scoped() -> None:
    assert gateway_container_name(7) == "revalid-retest-gw-7"
    assert sandbox_container_name(7) == "revalid-retest-sbx-7"


def test_resolve_scope_ips_strips_ports_and_dedupes() -> None:
    resolved = resolve_scope_ips(
        ("api.example.com:8443", "api.example.com"),
        resolver=lambda _h: ["1.1.1.1", "1.1.1.1", "2.2.2.2"],
    )
    # The two hosts collapse to one bare host; IPs are de-duped, order kept.
    assert resolved == {"api.example.com": ("1.1.1.1", "2.2.2.2")}


def test_resolve_scope_ips_maps_an_unresolvable_host_to_empty() -> None:
    resolved = resolve_scope_ips(("nope.invalid",), resolver=lambda _h: [])
    assert resolved == {"nope.invalid": ()}


def test_egress_firewall_allowlists_only_scope_ips_and_one_resolver() -> None:
    script = egress_firewall_script(("1.2.3.4", "5.6.7.8"), "9.9.9.9")
    # Default-drop is the foundation of the allowlist.
    assert "iptables -P OUTPUT DROP" in script
    # Each scoped IP is permitted for any protocol (so ICMP/UDP/TCP scans work).
    assert "iptables -A OUTPUT -d 1.2.3.4 -j ACCEPT" in script
    assert "iptables -A OUTPUT -d 5.6.7.8 -j ACCEPT" in script
    # Exactly the one resolver is allowed, on port 53 only.
    assert "iptables -A OUTPUT -d 9.9.9.9 -p udp --dport 53 -j ACCEPT" in script
    assert "iptables -A OUTPUT -d 9.9.9.9 -p tcp --dport 53 -j ACCEPT" in script
    # Return traffic and loopback are allowed; the namespace is kept alive.
    assert "--ctstate ESTABLISHED,RELATED -j ACCEPT" in script
    # IPv6 is closed entirely (v4-only scope), best-effort for a v6-less kernel.
    assert "ip6tables -P OUTPUT DROP 2>/dev/null || true" in script
    assert script.strip().endswith("exec sleep infinity")


class _FakeContainer:
    """Minimal docker-py container double that records its own removal."""

    def __init__(self, name: str | None) -> None:
        self.name = name
        self.removed = False

    def remove(self, *, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self._by_name: dict[str, _FakeContainer] = {}

    def run(self, image: str, **kwargs: Any) -> _FakeContainer:
        self.runs.append({"image": image, **kwargs})
        container = _FakeContainer(kwargs.get("name"))
        if container.name is not None:
            self._by_name[container.name] = container
        return container

    def get(self, name: str) -> _FakeContainer:
        import docker

        try:
            return self._by_name[name]
        except KeyError as exc:
            raise docker.errors.NotFound(name) from exc


class _FakeNetwork:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected: list[str] = []
        self.disconnected: list[str] = []
        self.removed = False

    def connect(self, container: str) -> None:
        self.connected.append(container)

    def disconnect(self, container: str, *, force: bool = False) -> None:
        self.disconnected.append(container)

    def remove(self) -> None:
        self.removed = True


class _FakeNetworks:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self._by_name: dict[str, _FakeNetwork] = {}

    def create(self, name: str, **kwargs: Any) -> _FakeNetwork:
        self.created.append({"name": name, **kwargs})
        network = _FakeNetwork(name)
        self._by_name[name] = network
        return network

    def get(self, name: str) -> _FakeNetwork:
        import docker

        try:
            return self._by_name[name]
        except KeyError as exc:
            raise docker.errors.NotFound(name) from exc


class _FakeDockerClient:
    def __init__(self) -> None:
        self.networks = _FakeNetworks()
        self.containers = _FakeContainers()


def test_online_gateway_owns_egress_and_the_sandbox_cannot_change_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway holds NET_ADMIN + the firewall; the sandbox shares its netns but not the cap.

    This is the whole point of ADR-0045: egress is enforced in a container the
    model cannot reach, so an approved command can use every tool against the
    scoped host yet cannot widen scope. Uses a fake Docker client (no daemon);
    DNS is stubbed so the test does no real lookup.
    """
    pytest.importorskip("docker")
    monkeypatch.setattr("revalid.sandbox._getaddrinfo_ips", lambda _h: ["9.9.9.9"])
    box = DockerSandbox(session_id=5)
    client = _FakeDockerClient()

    box._start_online(client, ("www.hackthissite.org",))

    gateway, sandbox = client.containers.runs[0], client.containers.runs[1]

    # Gateway: owns the firewall (entrypoint runs iptables) and the only NET_ADMIN.
    assert gateway["name"] == gateway_container_name(5)
    assert gateway["entrypoint"][:2] == ["sh", "-c"]
    assert "iptables" in gateway["entrypoint"][2]
    assert gateway["cap_add"] == ["NET_ADMIN"]
    assert gateway["dns"] == [dns_resolver()]
    # The scope host is pinned so name resolution needs no DNS.
    assert gateway["extra_hosts"] == {"www.hackthissite.org": "9.9.9.9"}

    # Sandbox: shares the gateway's netns, keeps NET_RAW (SYN scans) but NOT
    # NET_ADMIN — so no command it runs can flush the rules.
    assert sandbox["name"] == sandbox_container_name(5)
    assert sandbox["network_mode"] == f"container:{gateway_container_name(5)}"
    assert sandbox["cap_add"] == ["NET_RAW"]
    assert "NET_ADMIN" not in sandbox["cap_add"]
    # network_mode=container forbids these — they belong to the namespace owner.
    assert "network" not in sandbox
    assert "dns" not in sandbox

    # The per-session network is a normal bridge (online egress), not internal.
    assert client.networks.created[0]["name"] == internal_network_name(5)
    assert client.networks.created[0].get("internal") is not True


def test_online_fails_closed_when_the_scope_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No IP → no sandbox. Never open egress to a guessed address."""
    pytest.importorskip("docker")
    monkeypatch.setattr("revalid.sandbox._getaddrinfo_ips", lambda _h: [])
    box = DockerSandbox(session_id=6)
    client = _FakeDockerClient()

    with pytest.raises(SandboxUnavailableError, match="could not resolve"):
        box._start_online(client, ("nope.invalid",))

    # Nothing was provisioned before the failure.
    assert client.containers.runs == []


def test_lab_start_names_the_sandbox_container() -> None:
    """The lab path names its container too, so teardown-by-name reaps it."""
    pytest.importorskip("docker")
    box = DockerSandbox(session_id=8)
    client = _FakeDockerClient()

    box._start_lab(client)

    assert client.containers.runs[0]["name"] == sandbox_container_name(8)
    assert client.networks.created[0]["internal"] is True


def test_teardown_by_name_removes_containers_then_the_network() -> None:
    """stop()/cleanup work by name, so a fresh object reaps resources it never held."""
    pytest.importorskip("docker")
    client = _FakeDockerClient()
    sbx = client.containers.run("img", name="revalid-retest-sbx-9")
    gw = client.containers.run("img", name="revalid-retest-gw-9")
    net = client.networks.create("revalid-retest-9")

    _teardown_by_name(
        client,
        "revalid-retest-9",
        ("revalid-retest-sbx-9", "revalid-retest-gw-9"),
        "revalid-juice-shop",
    )

    assert sbx.removed and gw.removed
    assert net.removed


def test_teardown_by_name_tolerates_everything_already_gone() -> None:
    """Reaping a session whose resources never existed is a silent no-op."""
    pytest.importorskip("docker")
    client = _FakeDockerClient()
    _teardown_by_name(client, "revalid-retest-404", ("sbx", "gw"), "revalid-juice-shop")
