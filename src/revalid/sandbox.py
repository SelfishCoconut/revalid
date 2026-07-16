"""FR-17 / M6 egress-locked retest sandbox (ADR-0025, Slice 0).

An ephemeral Docker container on an ``--internal`` network (no host/internet
route) in which the retest agent runs one approved command at a time. The pure
surface (``CommandResult``, ``FakeSandbox``, helpers) is unit-tested; the live
``DockerSandbox`` needs the optional ``sandbox`` extra and is covered only by
the nightly system test — mirroring ``browser.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover - typing only
    import docker
    from docker.models.containers import Container

#: Pinned sandbox image (pentest CLIs: curl, etc.). Kept minimal for Slice 0.
DEFAULT_SANDBOX_IMAGE = "curlimages/curl:8.11.1"
#: The lab container name to attach to the internal network (lab/docker-compose.yml).
DEFAULT_LAB_CONTAINER = "revalid-juice-shop"


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


class FakeSandbox:
    """A scripted in-memory sandbox for unit/integration tests (no Docker)."""

    def __init__(self, script: list[CommandResult] | Callable[[str], CommandResult]) -> None:
        """Store the scripted results (or callable) to replay in :meth:`exec`."""
        self._script = script
        self.commands: list[str] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        """Mark the fake as started."""
        self.started = True

    def exec(self, command: str, *, timeout: float) -> CommandResult:
        """Return the next scripted result (or apply the callable)."""
        self.commands.append(command)
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
        image: str = DEFAULT_SANDBOX_IMAGE,
        lab_container: str = DEFAULT_LAB_CONTAINER,
    ) -> None:
        """Bind this sandbox to ``session_id`` (scopes its egress-locked network name)."""
        self._session_id = session_id
        self._image = image
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
        """Run ``command`` inside the live container and capture stdout/stderr/exit code."""
        import time

        if self._container is None:
            raise SandboxUnavailableError("sandbox not started")
        start = time.monotonic()
        code, output = self._container.exec_run(["sh", "-c", command], demux=True)
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
