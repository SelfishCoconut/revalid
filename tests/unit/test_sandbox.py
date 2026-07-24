"""Unit tests for the FR-17 egress-locked retest sandbox (ADR-0025, Slice 0).

Only the pure surface is exercised here: ``FakeSandbox`` (a scripted in-memory
double, no Docker), and the pure helpers. The live ``DockerSandbox`` needs the
optional ``sandbox`` extra and a Docker daemon — it is covered by the nightly
system test.
"""

from __future__ import annotations

import pytest

from revalid.sandbox import (
    EGRESS_PROXY_PORT,
    CommandResult,
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
