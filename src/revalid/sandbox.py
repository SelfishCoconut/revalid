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
#: Egress-proxy image for online-scope retests (ADR-0041): a deny-all-by-default
#: allowlisting HTTP(S) proxy that the sandbox's *only* route to the internet
#: passes through. Configurable so an operator can vendor their own; default Squid.
DEFAULT_EGRESS_PROXY_IMAGE = "ubuntu/squid:latest"
#: Env var overriding the egress-proxy image.
EGRESS_PROXY_IMAGE_ENV = "REVALID_EGRESS_PROXY_IMAGE"
#: Port the egress proxy listens on inside the session network.
EGRESS_PROXY_PORT = 3128


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


def egress_proxy_image() -> str:
    """Return the egress-proxy image (``$REVALID_EGRESS_PROXY_IMAGE`` or default)."""
    return os.environ.get(EGRESS_PROXY_IMAGE_ENV, DEFAULT_EGRESS_PROXY_IMAGE)


def egress_proxy_name(session_id: int) -> str:
    """Return the per-session egress-proxy container name (ADR-0041)."""
    return f"revalid-retest-proxy-{session_id}"


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


def squid_allowlist_config(hosts: tuple[str, ...]) -> str:
    """Build a deny-all-by-default Squid config allowing only ``hosts`` (ADR-0041).

    Each scope host becomes an exact ``dstdomain`` ACL (the port, if any, is
    dropped — Squid matches the hostname); everything not on the list is denied,
    so the proxy is a closed allowlist, not an open relay.

    Args:
        hosts: The online scope hosts (``host`` or ``host:port``) to permit.

    Returns:
        A Squid configuration string.
    """
    domains = " ".join(sorted({host.rsplit(":", 1)[0] for host in hosts if host}))
    return "\n".join(
        [
            f"http_port {EGRESS_PROXY_PORT}",
            f"acl scoped dstdomain {domains}",
            "http_access allow scoped",
            "http_access deny all",
            "shutdown_lifetime 1 second",
        ]
    )


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


class DockerSandbox:  # pragma: no cover - drives a live Docker daemon; covered by the system test
    """A real ephemeral Docker sandbox on an egress-locked ``--internal`` network."""

    def __init__(
        self,
        session_id: int,
        *,
        image: str | None = None,
        lab_container: str = DEFAULT_LAB_CONTAINER,
    ) -> None:
        """Bind this sandbox to ``session_id`` (scopes its egress-locked network name)."""
        self._session_id = session_id
        self._image = image if image is not None else sandbox_image()
        self._lab_container = lab_container
        self._container: Container | None = None
        self._proxy: Container | None = None
        self._network_name = internal_network_name(session_id)
        self._proxy_name = egress_proxy_name(session_id)

    def start(self, scope_hosts: tuple[str, ...] = ()) -> None:
        """Provision the sandbox for this session's scope (ADR-0041).

        Lab scope (empty, or every host is the lab host) keeps the unchanged
        ``--internal`` network with the lab container attached. Any other host is an
        online target: provision a deny-all-by-default egress proxy that allowlists
        only the scoped host(s) and route the sandbox's HTTP(S) through it — the
        sandbox has no other route out (the session network stays ``--internal``).
        """
        try:
            import docker
        except ImportError as exc:
            raise SandboxUnavailableError(
                "the sandbox extra is required: `uv sync --extra sandbox`"
            ) from exc
        client = docker.from_env()
        self._require_image(client)
        self._clear_stale_network(client)
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
            command="sleep infinity",
            network=self._network_name,
            detach=True,
            auto_remove=False,
            network_disabled=False,
        )

    def _start_online(self, client: docker.DockerClient, hosts: tuple[str, ...]) -> None:
        """Online provisioning (ADR-0041): an allowlisting egress proxy, fail-closed.

        The session network stays ``--internal`` so the sandbox has no direct
        internet route; a Squid proxy attached to *both* that network and the
        internet-capable default ``bridge`` is its only way out, and Squid denies
        every destination but ``hosts``. Any provisioning failure raises
        ``SandboxUnavailableError`` — the sandbox is never left with open egress.
        """
        import docker.errors

        try:
            client.networks.create(self._network_name, driver="bridge", internal=True)
            self._proxy = client.containers.run(
                egress_proxy_image(),
                name=self._proxy_name,
                command=["sh", "-c", self._proxy_launch(hosts)],
                network=self._network_name,
                detach=True,
                auto_remove=False,
            )
            client.networks.get("bridge").connect(self._proxy)  # the internet side
            self._proxy.reload()
            proxy_ip = self._proxy.attrs["NetworkSettings"]["Networks"][self._network_name][
                "IPAddress"
            ]
            proxy_url = f"http://{proxy_ip}:{EGRESS_PROXY_PORT}"
            self._container = client.containers.run(
                self._image,
                command="sleep infinity",
                network=self._network_name,
                detach=True,
                auto_remove=False,
                network_disabled=False,
                environment={
                    "http_proxy": proxy_url,
                    "https_proxy": proxy_url,
                    "HTTP_PROXY": proxy_url,
                    "HTTPS_PROXY": proxy_url,
                },
            )
        except (docker.errors.APIError, KeyError) as exc:
            self.stop()  # fail closed: never leave a half-provisioned open route
            raise SandboxUnavailableError(
                f"online egress proxy provisioning failed: {exc}"
            ) from exc

    def _proxy_launch(self, hosts: tuple[str, ...]) -> str:
        """A shell one-liner that writes the allowlist config and runs Squid in foreground."""
        config = squid_allowlist_config(hosts)
        return (
            f"printf '%s' {config!r} > /etc/squid/squid.conf && "
            "exec squid -N -f /etc/squid/squid.conf"
        )

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

    def _clear_stale_network(self, client: docker.DockerClient) -> None:
        """Remove a same-named network left over from a crashed prior session, if any.

        ``start()`` is not re-entrant across a crash: a session killed before it
        reaches ``stop()`` (process kill, daemon restart) leaves its internal
        network behind. Because the network name is session-scoped, retrying
        ``start()`` for the same ``session_id`` would otherwise hit a 409
        conflict on ``networks.create`` forever. Self-heal instead: disconnect
        any leftover endpoints and remove the stale network first.

        Safety assumption (ADR-0008, single trusted user): this targets a
        leftover from a *prior, crashed* session of the *same* ``session_id`` —
        it does not distinguish that from a network belonging to another,
        currently-live session that happens to share the same id. That's safe
        here only because ``session_id`` is a unique DB row id and sessions run
        sequentially (never concurrently) under the single-user model. It would
        be unsafe to run two sandboxes with the same ``session_id`` concurrently
        (e.g. the system test's fixed sentinel id 9999 must never be run
        concurrently with itself) — doing so would let one instance's
        ``start()`` tear down the other's live network.
        """
        import docker

        try:
            stale = client.networks.get(self._network_name)
        except docker.errors.NotFound:
            return
        stale.reload()
        for container_id in list(stale.attrs.get("Containers") or {}):
            try:
                stale.disconnect(container_id, force=True)
            except docker.errors.APIError:
                pass
        stale.remove()

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
        """Remove the container and tear down the egress-locked network (best-effort).

        Each step is independently tolerant of "already gone": the container may
        have vanished already (crash, an external ``docker rm``, a daemon restart)
        raising ``NotFound``/``APIError`` on ``.remove()``, and ``disconnect``
        raises a non-``NotFound`` ``APIError`` when the lab container was never
        actually connected (e.g. ``start()`` failed before ``network.connect``).
        Neither must prevent the remaining teardown steps from running.
        """
        import docker

        if self._container is not None:
            try:
                self._container.remove(force=True)
            except docker.errors.APIError:
                pass
            self._container = None
        # The online-scope egress proxy (ADR-0041), if any: remove it before the
        # network so its dual attachment (session net + bridge) can't block removal.
        if self._proxy is not None:
            try:
                self._proxy.remove(force=True)
            except docker.errors.APIError:
                pass
            self._proxy = None
        client = docker.from_env()
        try:
            network = client.networks.get(self._network_name)
        except docker.errors.NotFound:
            return
        try:
            network.disconnect(self._lab_container, force=True)
        except docker.errors.APIError:
            pass
        try:
            network.remove()
        except docker.errors.NotFound:
            pass
