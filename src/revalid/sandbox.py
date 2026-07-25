"""FR-17 / M6 egress-locked retest sandbox (ADR-0025, Slice 0).

An ephemeral Docker container on an ``--internal`` network (no host/internet
route) in which the retest agent runs one approved command at a time. The pure
surface (``CommandResult``, ``FakeSandbox``, helpers) is unit-tested; the live
``DockerSandbox`` needs the optional ``sandbox`` extra and is covered only by
the nightly system test.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover - typing only
    import docker
    from docker.models.containers import Container

#: Pinned sandbox image: the Kali-based pentest toolbox built from
#: ``lab/sandbox/Dockerfile`` by ``make sandbox-image`` (issue #105). It is built
#: locally rather than pulled because the toolbox is curated here; the tag is
#: pinned so a rebuild is a deliberate act.
DEFAULT_SANDBOX_IMAGE = "revalid-sandbox:1.0"
#: Env var overriding the sandbox image — point the agent at your own toolbox.
SANDBOX_IMAGE_ENV = "REVALID_SANDBOX_IMAGE"
#: The lab container name to attach to the internal network (lab/docker-compose.yml).
DEFAULT_LAB_CONTAINER = "revalid-juice-shop"
#: Env var overriding the lab target base URL (host-side polling in system tests).
LAB_BASE_URL_ENV = "REVALID_LAB_BASE_URL"
#: Default lab target base URL (the local Juice Shop; see lab/docker-compose.yml).
DEFAULT_LAB_BASE_URL = "http://localhost:3000"
#: Exit codes a command exits with when the in-container ``timeout`` wrapper kills
#: it for overrunning its limit: 124 (coreutils), 143 (busybox SIGTERM), 137
#: (SIGKILL). ``run_command`` maps these to a "timed out" note for the agent.
TIMEOUT_EXIT_CODES = frozenset({124, 137, 143})
#: DNS resolver an online sandbox is allowed to reach for name resolution
#: (ADR-0045). The scope host is pre-resolved and pinned in ``/etc/hosts``, but
#: tools that run their own resolver (nmap most notably) bypass that file, so a
#: single resolver is allowlisted for them. The L3 firewall still permits only
#: the scoped IPs, so a resolved-but-off-scope address is dropped regardless.
DEFAULT_DNS_RESOLVER = "1.1.1.1"
#: Env var overriding the allowed DNS resolver.
DNS_RESOLVER_ENV = "REVALID_DNS_RESOLVER"


class CommandResult(BaseModel):
    """The captured result of one command run in the sandbox."""

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int


class Sandbox(Protocol):
    """One ephemeral, egress-locked execution environment for a retest session."""

    def start(self, scope_hosts: tuple[str, ...] = ()) -> None:
        """Provision the environment for ``scope_hosts`` (idempotent).

        ``scope_hosts`` is the session's parsed scope (ADR-0041): empty or the lab
        host keeps lab provisioning; any other host provisions egress to it.
        """

    def exec(self, command: str, *, timeout: float) -> CommandResult:
        """Run ``command`` and capture its result."""

    def stop(self) -> None:
        """Tear the environment down; nothing persists."""


#: Builds a session-scoped ``Sandbox`` on demand (the live factory constructs a
#: ``DockerSandbox(session_id)`` per retest session; tests inject a ``FakeSandbox``).
SandboxFactory = Callable[[int], Sandbox]


class SandboxUnavailableError(Exception):
    """Raised when a sandbox is required but the runtime cannot provide one."""


def internal_network_name(session_id: int) -> str:
    """Return the per-session egress-locked Docker network name."""
    return f"revalid-retest-{session_id}"


def egress_probe_command(host: str) -> str:
    """Return a command that fails iff ``host`` is unreachable (egress-lock test)."""
    return f"curl --max-time 5 --silent --show-error --output /dev/null https://{host}"


def lab_base_url() -> str:
    """Return the lab target base URL (``$REVALID_LAB_BASE_URL`` or the default)."""
    return os.environ.get(LAB_BASE_URL_ENV, DEFAULT_LAB_BASE_URL)


def sandbox_image() -> str:
    """Return the sandbox image to run (``$REVALID_SANDBOX_IMAGE`` or the default).

    The default is the locally-built Kali toolbox (issue #105); the override
    exists so an operator can point the agent at their own image without a code
    change — the egress lock is enforced by the network, not by the image, so
    swapping it changes the tools available and nothing about containment.
    """
    return os.environ.get(SANDBOX_IMAGE_ENV, DEFAULT_SANDBOX_IMAGE)


def dns_resolver() -> str:
    """Return the allowed DNS resolver (``$REVALID_DNS_RESOLVER`` or the default)."""
    return os.environ.get(DNS_RESOLVER_ENV, DEFAULT_DNS_RESOLVER)


def sandbox_container_name(session_id: int) -> str:
    """Return the per-session sandbox container name (ADR-0045).

    Named (rather than anonymous) so teardown can find and remove it by name even
    when no live ``DockerSandbox`` object holds a reference — e.g. reaping a
    session orphaned by a backend restart when its report is deleted.
    """
    return f"revalid-retest-sbx-{session_id}"


def gateway_container_name(session_id: int) -> str:
    """Return the per-session egress-gateway container name (ADR-0045).

    The gateway owns the network namespace and the iptables egress allowlist; the
    sandbox joins that namespace but cannot alter it (it holds no ``NET_ADMIN``).
    """
    return f"revalid-retest-gw-{session_id}"


def lab_host() -> str:
    """Return the lab target's host (``host`` or ``host:port``) from the lab base URL."""
    from revalid.scope import scope_host

    return scope_host(lab_base_url()) or ""


def is_lab_scope(scope_hosts: tuple[str, ...]) -> bool:
    """Whether a scope stays on the lab (empty, or every host is the lab host).

    Lab scope keeps the unchanged ``--internal`` + attached-lab-container
    provisioning; any other host is an online target that needs the egress
    proxy (ADR-0041). An empty scope defaults to the lab.
    """
    lab = lab_host()
    return all(host == lab for host in scope_hosts)


def online_scope_hosts(scope_hosts: tuple[str, ...]) -> tuple[str, ...]:
    """The non-lab hosts in a scope — the online targets to allowlist (ADR-0041)."""
    lab = lab_host()
    return tuple(host for host in scope_hosts if host != lab)


def _bare_host(host: str) -> str:
    """Strip a ``:port`` suffix from a scope host, leaving the bare hostname."""
    return host.rsplit(":", 1)[0] if ":" in host and not host.endswith(":") else host


def resolve_scope_ips(
    hosts: tuple[str, ...],
    resolver: Callable[[str], list[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Resolve each online scope host to its IP addresses (ADR-0045).

    The IPs are pinned into the sandbox at launch — allowlisted in the egress
    firewall and written to ``/etc/hosts`` — so the L3 gateway can permit exactly
    the scoped host and nothing else. All A/AAAA records are taken, but they are a
    *snapshot*: a target behind rotating CDN IPs may present addresses not seen
    here (the inherent limit of an IP allowlist vs. the retired L7 proxy — stated
    in ADR-0045).

    Args:
        hosts: The online scope hosts (``host`` or ``host:port``); the port is
            dropped for resolution.
        resolver: Injection seam for tests — maps a bare host to its IP strings.
            Defaults to a real DNS lookup via :func:`socket.getaddrinfo`.

    Returns:
        A mapping of bare host → its resolved IPs (in stable, de-duplicated order).
        A host that does not resolve maps to an empty tuple (the caller fails
        closed on it rather than opening egress to a guessed address).
    """
    resolve = resolver if resolver is not None else _getaddrinfo_ips
    resolved: dict[str, tuple[str, ...]] = {}
    for host in hosts:
        bare = _bare_host(host)
        if not bare:
            continue
        ips = tuple(dict.fromkeys(resolve(bare)))  # de-dupe, preserve order
        resolved[bare] = ips
    return resolved


def _getaddrinfo_ips(host: str) -> list[str]:  # pragma: no cover - real DNS
    """Resolve ``host`` to its **IPv4** addresses via the system resolver.

    IPv4 only, on purpose: Docker's default bridge is v4-only, so a host's AAAA
    (IPv6) records are unreachable from the sandbox anyway — and worse, an IPv6
    address fed to ``iptables`` (which is v4-only; ``ip6tables`` handles v6) makes
    the gateway's firewall script fail and the sandbox lose all egress. The
    firewall separately blanket-drops any IPv6 as defence-in-depth. A v6-only host
    resolves to nothing here and the caller fails closed on it.
    """
    import socket

    infos = socket.getaddrinfo(host, None, socket.AF_INET)
    return [str(info[4][0]) for info in infos]


def egress_firewall_script(scope_ips: tuple[str, ...], dns_ip: str) -> str:
    """Build the gateway's iptables egress allowlist as a shell script (ADR-0045).

    Default-drop OUTPUT (IPv4), then allow only: loopback, established/related
    return traffic, DNS to the one permitted resolver, and each scoped IP (any
    protocol, so ICMP/UDP/TCP scans all work). IPv6 is blanket-dropped — the
    scope IPs are v4 (see :func:`_getaddrinfo_ips`) and Docker's bridge is v4-only,
    so v6 is both unneeded and a leak to close; the drop is best-effort (``|| true``)
    so a kernel without a v6 stack does not abort the script. Everything else is
    dropped, so the sandbox sharing this namespace reaches the scoped host and
    nothing else. The gateway then blocks on ``sleep infinity`` to keep the
    namespace (and thus its rules) alive for the sandbox's lifetime.

    Only resolved IPs and a resolver IP are interpolated — never a hostname — so
    there is no shell-injection surface (all inputs are numeric).

    Args:
        scope_ips: The resolved (IPv4) scope IPs to permit.
        dns_ip: The single DNS resolver to permit on port 53.

    Returns:
        A ``sh``-executable script string.
    """
    lines = [
        "set -e",
        "iptables -P OUTPUT DROP",
        "iptables -A OUTPUT -o lo -j ACCEPT",
        "iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        f"iptables -A OUTPUT -d {dns_ip} -p udp --dport 53 -j ACCEPT",
        f"iptables -A OUTPUT -d {dns_ip} -p tcp --dport 53 -j ACCEPT",
    ]
    lines += [f"iptables -A OUTPUT -d {ip} -j ACCEPT" for ip in scope_ips]
    # Close IPv6 entirely (best-effort: a v6-less kernel has no ip6tables table).
    lines.append("ip6tables -P OUTPUT DROP 2>/dev/null || true")
    lines.append("exec sleep infinity")
    return "\n".join(lines)


class FakeSandbox:
    """A scripted in-memory sandbox for unit/integration tests (no Docker)."""

    def __init__(self, script: list[CommandResult] | Callable[[str], CommandResult]) -> None:
        """Store the scripted results (or callable) to replay in :meth:`exec`."""
        self._script = script
        self.commands: list[str] = []
        #: The per-command timeouts passed to :meth:`exec`, in order — lets tests
        #: assert the agent-chosen ``timeout_seconds`` reaches the sandbox (#150).
        self.timeouts: list[float] = []
        self.started = False
        self.stopped = False
        #: The scope hosts the orchestrator provisioned with — lets tests assert the
        #: parsed finding scope reaches the sandbox (ADR-0041).
        self.scope_hosts: tuple[str, ...] = ()

    def start(self, scope_hosts: tuple[str, ...] = ()) -> None:
        """Mark the fake as started, recording the scope it was provisioned for."""
        self.started = True
        self.scope_hosts = scope_hosts

    def exec(self, command: str, *, timeout: float) -> CommandResult:
        """Return the next scripted result (or apply the callable)."""
        self.commands.append(command)
        self.timeouts.append(timeout)
        if callable(self._script):
            return self._script(command)
        if not self._script:
            raise SandboxUnavailableError("FakeSandbox script exhausted")
        return self._script.pop(0)

    def stop(self) -> None:
        """Mark the fake as stopped."""
        self.stopped = True


def _teardown_by_name(
    client: docker.DockerClient,
    network_name: str,
    container_names: tuple[str, ...],
    lab_container: str,
) -> None:
    """Remove a session's containers then its network, all by name (best-effort).

    Containers go first (one still attached blocks the network's removal), then the
    lab container is disconnected (online sessions never attach it, so the call is a
    harmless no-op there) and the network is removed. Every step tolerates the
    resource being already gone, so this is safe to call to reap an orphan, to
    self-heal a stale prior run, or as the normal ``stop()``. The gateway must be
    removed before the sandbox that shares its namespace would otherwise be fine —
    order within ``container_names`` is caller-chosen (sandbox first, gateway
    second) so the namespace owner outlives its guest.
    """
    import docker

    for name in container_names:
        try:
            client.containers.get(name).remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass
    try:
        network = client.networks.get(network_name)
    except docker.errors.NotFound:
        return
    try:
        network.disconnect(lab_container, force=True)
    except docker.errors.APIError:
        pass
    try:
        network.remove()
    except (docker.errors.NotFound, docker.errors.APIError):
        pass


class DockerSandbox:  # pragma: no cover - drives a live Docker daemon; covered by the system test
    """A real ephemeral Docker sandbox on an egress-locked ``--internal`` network."""

    def __init__(
        self,
        session_id: int,
        *,
        image: str | None = None,
        lab_container: str = DEFAULT_LAB_CONTAINER,
    ) -> None:
        """Bind this sandbox to ``session_id`` (scopes its per-session resource names)."""
        self._session_id = session_id
        self._image = image if image is not None else sandbox_image()
        self._lab_container = lab_container
        self._container: Container | None = None
        self._gateway: Container | None = None
        self._network_name = internal_network_name(session_id)
        self._sandbox_name = sandbox_container_name(session_id)
        self._gateway_name = gateway_container_name(session_id)

    def start(self, scope_hosts: tuple[str, ...] = ()) -> None:
        """Provision the sandbox for this session's scope (ADR-0025/0041/0045).

        Lab scope (empty, or every host is the lab host) keeps the unchanged
        ``--internal`` network with the lab container attached — the target is a
        named neighbour, reachable by construction and nothing else. Any other host
        is an online target: provision a per-session **egress gateway** that holds an
        L3 iptables allowlist for the scoped IP(s), and run the sandbox inside the
        gateway's network namespace so every tool (not just HTTP) can reach the
        scoped host and nothing else — and cannot alter that, because it holds no
        ``NET_ADMIN`` (ADR-0045).
        """
        try:
            import docker
        except ImportError as exc:
            raise SandboxUnavailableError(
                "the sandbox extra is required: `uv sync --extra sandbox`"
            ) from exc
        client = docker.from_env()
        self._require_image(client)
        self._clear_stale(client)
        if is_lab_scope(scope_hosts):
            self._start_lab(client)
        else:
            self._start_online(client, online_scope_hosts(scope_hosts))

    def _start_lab(self, client: docker.DockerClient) -> None:
        """Lab provisioning (unchanged): internal network + attached lab container."""
        network = client.networks.create(self._network_name, driver="bridge", internal=True)
        network.connect(self._lab_container)  # allowlist == network membership (FR-06)
        self._container = client.containers.run(
            self._image,
            name=self._sandbox_name,
            command="sleep infinity",
            network=self._network_name,
            detach=True,
            auto_remove=False,
            network_disabled=False,
        )

    def _start_online(self, client: docker.DockerClient, hosts: tuple[str, ...]) -> None:
        """Online provisioning (ADR-0045): a per-session L3 egress gateway, fail-closed.

        Resolve the scope host(s) to their IP(s), then provision two containers on
        a per-session bridge network:

        - a **gateway** that holds ``NET_ADMIN`` and installs an iptables OUTPUT
          allowlist permitting only the scoped IPs and one DNS resolver — default
          drop otherwise — then blocks to keep its network namespace alive;
        - the **sandbox** itself, run *inside the gateway's network namespace*
          (``network_mode=container:<gateway>``) with ``NET_RAW`` but **not**
          ``NET_ADMIN``. So the sandbox's every packet is filtered by the gateway's
          rules, all tools work (raw sockets, ICMP, any port on the scoped host),
          and no command it runs can change egress — the capability lives in a
          different container it cannot reach.

        The scoped host is pinned in the shared ``/etc/hosts`` (via the gateway's
        ``extra_hosts``) so name resolution needs no network; the one allowed
        resolver covers tools that resolve independently (nmap). Any provisioning
        failure tears everything down and raises — never a half-open route.
        """
        import docker.errors

        try:
            resolved = resolve_scope_ips(hosts)
            scope_ips = tuple(ip for ips in resolved.values() for ip in ips)
            if not scope_ips:
                raise SandboxUnavailableError(
                    f"could not resolve any scope host to an IP: {', '.join(hosts)}"
                )
            dns_ip = dns_resolver()
            client.networks.create(self._network_name, driver="bridge")
            self._gateway = client.containers.run(
                self._image,
                name=self._gateway_name,
                # `entrypoint`, not `command`: the run-command path must own PID 1
                # so its `exec sleep infinity` keeps the netns alive; the script is
                # a plain multi-line `sh -c` argument (docker-py passes argv, so no
                # shell requoting) and interpolates only numeric IPs — no injection.
                entrypoint=["sh", "-c", egress_firewall_script(scope_ips, dns_ip)],
                network=self._network_name,
                cap_add=["NET_ADMIN"],
                dns=[dns_ip],
                # Pin the scope host(s) so glibc-based tools resolve with zero DNS;
                # this /etc/hosts is shared into the sandbox via the netns join.
                extra_hosts={host: ips[0] for host, ips in resolved.items() if ips},
                detach=True,
                auto_remove=False,
            )
            self._container = client.containers.run(
                self._image,
                name=self._sandbox_name,
                command="sleep infinity",
                # Share the gateway's network namespace: the sandbox has no network
                # of its own, so the gateway's OUTPUT allowlist is the sandbox's
                # only route out. `network_mode=container` forbids network/dns/
                # extra_hosts kwargs — they belong to the namespace owner above.
                network_mode=f"container:{self._gateway_name}",
                cap_add=["NET_RAW"],  # SYN scans etc.; NOT NET_ADMIN (can't edit rules)
                detach=True,
                auto_remove=False,
            )
        except docker.errors.APIError as exc:
            self.stop()  # fail closed: never leave a half-provisioned open route
            raise SandboxUnavailableError(
                f"online egress gateway provisioning failed: {exc}"
            ) from exc

    def _require_image(self, client: docker.DockerClient) -> None:
        """Fail early and actionably when the toolbox image has not been built.

        The sandbox image is built locally (``make sandbox-image``), not pulled,
        so a fresh clone has no copy of it. Checking here turns a Docker
        ``ImageNotFound`` raised mid-launch into a message naming the command
        that fixes it.

        Deliberately not falling back to a smaller image: the agent would then
        silently lose nmap, sqlmap and the rest, and a retest that concludes
        ``fixed`` because its tool was missing is precisely the confidently-wrong
        verdict this project is built to avoid.
        """
        import docker.errors

        try:
            client.images.get(self._image)
        except docker.errors.ImageNotFound as exc:
            raise SandboxUnavailableError(
                f"sandbox image {self._image!r} is not built — run `make sandbox-image` "
                f"(or set ${SANDBOX_IMAGE_ENV} to an image you already have)"
            ) from exc

    def _clear_stale(self, client: docker.DockerClient) -> None:
        """Reap this session's leftover containers + network from a crashed prior run.

        ``start()`` is not re-entrant across a crash: a session killed before it
        reaches ``stop()`` (process kill, daemon restart) leaves its sandbox
        container, gateway container and/or network behind. All three are
        session-scoped by *name*, so retrying ``start()`` for the same
        ``session_id`` would hit a 409 name conflict forever. Self-heal by tearing
        down the same-named resources first — exactly the by-name teardown
        ``stop()`` performs.

        Safety assumption (ADR-0008, single trusted user): this targets leftovers
        from a *prior, crashed* run of the *same* ``session_id``. That is safe only
        because ``session_id`` is a unique DB row id and sessions run sequentially
        under the single-user model; two sandboxes sharing a ``session_id`` must
        never run concurrently (e.g. the system test's fixed sentinel id).
        """
        _teardown_by_name(
            client,
            self._network_name,
            (self._sandbox_name, self._gateway_name),
            self._lab_container,
        )

    def exec(self, command: str, *, timeout: float) -> CommandResult:
        """Run ``command`` inside the live container, capped at ``timeout`` seconds.

        Docker's ``exec_run`` is blocking (the model always sees the *complete*
        output before its next turn) but has **no native timeout**, so the cap is
        enforced in-container by wrapping the command with ``timeout`` — present on
        both the alpine/busybox base of the default image and a coreutils-based
        Kali image (#105). A command that overruns is killed and exits non-zero
        (124 on coreutils, 143/SIGTERM on busybox); ``run_command`` surfaces that to
        the agent, which chose the limit and can retry with a narrower scope. Without
        this, a hanging or unbounded command (e.g. an nmap sweep, or one blocked on
        stdin) would wedge the session at ``running_command`` forever.
        """
        import time

        if self._container is None:
            raise SandboxUnavailableError("sandbox not started")
        start = time.monotonic()
        wrapped = ["timeout", str(int(timeout)), "sh", "-c", command]
        code, output = self._container.exec_run(wrapped, demux=True)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        stdout, stderr = output
        return CommandResult(
            stdout=(stdout or b"").decode(errors="replace"),
            stderr=(stderr or b"").decode(errors="replace"),
            exit_code=code,
            elapsed_ms=elapsed_ms,
        )

    def stop(self) -> None:
        """Tear down this session's containers and network by name (best-effort).

        Removal is keyed on the session-scoped resource *names*, not on the
        object's held references, so a freshly constructed ``DockerSandbox`` can
        reap resources it never created — the case that matters when a report is
        deleted after a backend restart has forgotten the live session. Every step
        tolerates "already gone".
        """
        import docker

        self._container = None
        self._gateway = None
        _teardown_by_name(
            docker.from_env(),
            self._network_name,
            (self._sandbox_name, self._gateway_name),
            self._lab_container,
        )
