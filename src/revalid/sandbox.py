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


class CommandResult(BaseModel):
    """The captured result of one command run in the sandbox."""

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int


class Sandbox(Protocol):
    """One ephemeral, egress-locked execution environment for a retest session."""

    def start(self) -> None:
        """Provision the environment (idempotent)."""

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

    def start(self) -> None:
        """Mark the fake as started."""
        self.started = True

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
        self._network_name = internal_network_name(session_id)

    def start(self) -> None:
        """Create the internal network, attach the lab container, and launch the container."""
        try:
            import docker
        except ImportError as exc:
            raise SandboxUnavailableError(
                "the sandbox extra is required: `uv sync --extra sandbox`"
            ) from exc
        client = docker.from_env()
        self._require_image(client)
        self._clear_stale_network(client)
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
