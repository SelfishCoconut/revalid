# 0044. Containerised deployment: sibling sandbox containers over the host Docker socket

Date: 2026-07-25
Status: accepted (ratified 2026-07-25)

## Context

Running revalid has always meant running a development checkout: `uv sync
--extra sandbox`, `make build-ui`, `make run`, and `make lab-up` in another
shell. For a v1.0 that is meant to be *deployable* — handed to someone, or
brought up on another machine — that is too much apparatus, and it silently
depends on a correct local toolchain (Python 3.12, Node 22, uv, the right
extras).

The obvious answer is an image plus a compose file. What makes it a decision
rather than a chore is that **revalid runs Docker itself**. The agentic retest
(ADR-0025) provisions, per session, an `--internal` network and a sandbox
container from the Kali toolbox image, attaching the target — the lab container,
or a Squid egress proxy for an online scope (ADR-0041). Containerising the app
therefore forces a choice about how the containerised app reaches a Docker
daemon, and that choice has a real security consequence.

Three options:

1. **Docker-in-Docker.** Run a nested daemon inside the app container. Needs
   `--privileged`, which is strictly *more* dangerous than the alternative,
   duplicates the image cache, and breaks the lab-container attachment: a nested
   daemon cannot see `revalid-juice-shop` running on the host.
2. **A socket proxy** (e.g. `tecnativa/docker-socket-proxy`), exposing only the
   daemon endpoints the app uses. Genuinely reduces the granted authority, but
   the app needs container create/start/exec/remove *and* network
   create/connect/remove — nearly the whole dangerous surface — so the residual
   reduction is small against a real extra moving part.
3. **Mount the host socket** and let the app create *sibling* containers.

## Decision

**Mount `/var/run/docker.sock` and provision siblings** (option 3).

The retest sandbox stays exactly what it was: a container on a per-session
`--internal` network, created by the *host* daemon, with the lab container or
the egress proxy as its only other member. Nothing about the containment model
in ADR-0025/0033/0041 changes — the egress lock is a property of the network,
and the network is unaffected by where the process that asked for it runs. This
was verified rather than assumed (below).

**What this grants, stated plainly.** The Docker socket is a root-equivalent
interface: a process that can talk to it can start a privileged container that
mounts the host filesystem. Mounting it into `revalid-app` means the application
container has full control of the host's Docker, and therefore of the host.

We accept this because of the threat model already recorded in ADR-0008: revalid
is a single-operator local instrument, run by the person who owns the machine,
who *already* runs `make lab-up` and `make sandbox-image` with exactly that
authority. Containerising does not hand new power to a new party; it moves the
same operator's own authority behind a different door. A multi-tenant or hosted
deployment would invalidate this and should revisit option 2 — but such a
deployment would also invalidate NFR-03 and the single-user model wholesale, so
it is a different product, not a configuration change.

**The image.** Two stages: Node 22 builds the SPA (FR-11), Python 3.12 installs
the locked dependency set with `--extra sandbox` and `--no-dev`, then the SPA
build is copied in beside the package. The application resolves the SPA from its
own package location, so a build-time assertion fails the image if the build did
not land where the app looks — rather than serving a blank page at runtime.

**State.** `create_app` opens `revalid.db` relative to the process working
directory, so the container's working directory *is* `/data`, a named volume.
The database therefore survives a rebuild without the application needing any
notion of being containerised, and with no new configuration surface.

**The LLM stays on the host.** The compose stack does not run Ollama. It sets
`OLLAMA_BASE_URL` to `host.docker.internal` (via a `host-gateway` entry), which
seeds the settings row on a fresh database only (ADR-0021) — afterwards the
operator's saved setting wins. This keeps model weights, which are gigabytes and
usually already present, out of the deployment, and reuses whatever backend the
operator already runs.

**The lab is included, not copied.** The root compose file `include`s
`lab/docker-compose.yml` rather than restating the Juice Shop service, because
the pinned `v17.1.1` tag is evaluation ground truth (FR-15) and must have one
source of truth.

**Ports.** Both services publish to `127.0.0.1` only (NFR-03). The application's
port is `${REVALID_PORT:-8000}` so the stack can run beside a `make run` dev
server; it is loopback-bound whatever the value. Binding `0.0.0.0` *inside* the
container is the container's own namespace, not a host exposure.

**The sandbox image is a prerequisite, not a service.** `revalid-sandbox:1.0` is
an image the app runs, not something compose starts, so `make deploy` builds it
first — a missing image would otherwise fail only later, when the operator
starts their first retest.

## Consequences

- One command (`make deploy`) brings up the whole tool; the operator needs only
  Docker, not a Python or Node toolchain.
- The application container is root-equivalent on the host. This is written on
  the compose volume mount as well as here, so it cannot be changed casually.
- The retest containment story is unchanged and was re-verified inside the
  deployed stack: from `revalid-app`, a real `DockerSandbox` reached the lab
  container (HTTP 200) while `example.com` failed to resolve (curl exit 6).
- `docker compose down` keeps the database (named volume); `down -v` drops it.
- Running the stack and `make run` at once needs `REVALID_PORT` set, since both
  otherwise want 8000.
- The image does not contain the sandbox toolbox or the lab; all three are
  separate images the host daemon runs side by side.

## Alternatives considered

- **Docker-in-Docker** — rejected: needs `--privileged` (worse than the socket
  it replaces) and a nested daemon cannot attach the host's lab container.
- **Socket proxy with an endpoint allowlist** — rejected for now: the app needs
  container create/start/exec/remove and network create/connect/remove, so the
  allowlist would cover nearly the whole dangerous surface for a real extra
  component. The right move if the threat model ever stops being ADR-0008.
- **Bundling Ollama as a service** — rejected: pulls gigabytes of weights into a
  stack whose operator almost always has a backend already, and duplicates it.
- **Publishing the image to a registry** — out of scope for v1.0; the image is
  built locally from a lockfile, which is reproducible and needs no account.
