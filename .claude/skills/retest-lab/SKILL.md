---
name: retest-lab
description: Bring up / tear down the local vulnerable lab targets (docker compose) used for developing and system-testing the retest engine. Use for "start the lab", "run system tests against the lab", or adding a new lab target.
---

# Retest lab

Local, intentionally vulnerable targets for safely developing and testing `revalid`. **The only systems this project ever retests are the ones defined here** (plus anything Álvaro explicitly authorizes in writing).

## How containment actually works (read this first)

There is **no target allowlist**. `src/revalid/allowlist.py` was deleted in ADR-0033; nothing inspects a target string or refuses a hostname.

Confinement is Docker network topology (`src/revalid/sandbox.py`). Per retest session:

1. An `internal=True` bridge network is created — Docker installs no gateway and drops off-bridge traffic, so there is **no route** to the host, the LAN, or the internet.
2. The lab container is attached to that network **by name**, and the agent's container is launched with it as its only interface.

Membership *is* the authorization. This is stronger than a string check — `curl`, `nmap`, proxies, IPv6 and `bash -c` are all equally powerless — but it has hard limits that shape how you use the lab:

- The only reachable target is the container named `revalid-juice-shop` (`DEFAULT_LAB_CONTAINER`, hardcoded; `app.py` builds `DockerSandbox(sid)` with no override).
- **Public IPs are unreachable by construction.** There is no design in place for retesting a real external host; that needs an egress-proxy or firewall design first.
- **The host's own `localhost:3000` is also unreachable.** Inside the sandbox, `localhost` is the sandbox container.
- The lab answers only at `http://revalid-juice-shop:3000`, via Docker's embedded DNS on the internal bridge.

That last pair is why a retest scoped to `http://localhost:3000/...` — which is what a report's `affected_endpoints` usually say — cannot connect. Scope is operator-validated and never silently rewritten, so when a session's probes all fail to connect, check the scope before believing the verdict.

Two documented gaps in the isolation, worth knowing and not currently fixed:

- The lab container keeps its original `lab_default` network as well, so it retains internet and host-gateway routing. The sandbox cannot route through it, but code the agent *successfully executes on the target* is not confined.
- `internal=True` does not block DNS on hosts whose resolver is a loopback stub (systemd-resolved — Debian/Ubuntu defaults and the CI runners). Docker proxies those queries from the daemon's namespace.

## Current shape

- `lab/docker-compose.yml` defines **OWASP Juice Shop** pinned to `v17.1.1`, container `revalid-juice-shop`, bound to `127.0.0.1:3000` only.
- `make lab-up` starts it and polls `http://localhost:3000/rest/admin/application-version` until it answers; `make lab-down` stops and removes it.
- `make sandbox-image` builds the agent's Kali toolbox (`lab/sandbox/Dockerfile`) as `revalid-sandbox:1.0`. It is built locally, never pulled — the sandbox is egress-locked, so every tool must be baked in. Override with `$REVALID_SANDBOX_IMAGE`.
- `make test-system` runs the `system`-marked tests; they **skip** if Docker or the lab is unreachable (so the unit/integration suite stays green without Docker). `system-tests.yml` (nightly) builds the image and brings the lab up first.
- `make demo-retest-session` drives a full propose → approve → verdict cycle offline — no Docker, no lab, no LLM.

## Adding a target

1. Add the service to `lab/docker-compose.yml` (pin the image tag — system-test ground truth depends on the version). Bind to `127.0.0.1` only.
2. Give it a stable `container_name` and make the sandbox attach it: today `DEFAULT_LAB_CONTAINER` in `src/revalid/sandbox.py` names exactly one container, so a second target needs that made configurable first. **A target not attached to the session's internal network is unreachable — silently, as a connection failure, not as a refusal.**
3. Address it from the agent by container name (`http://<container_name>:<port>`), never `localhost`.
4. Add a findings fixture under `tests/data/` and a `system`-marked test asserting the expected verdict.

## Safety rules (always in force)

- Lab binds to localhost only. Never expose lab ports beyond the host.
- Retests reach only what is attached to the session's internal network. Do not widen that to a non-internal network, or to a target outside `lab/`, without an ADR.
- No destructive payloads in retest checks: verification probes only (presence/absence of vulnerability), never exploitation for impact.
- If a requirement ever calls for testing against non-lab systems — including any real public IP — stop and require written authorization recorded as an ADR before implementing anything.
