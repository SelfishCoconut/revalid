---
name: retest-lab
description: Bring up / tear down the local vulnerable lab targets (docker compose) used for developing and system-testing the retest engine. Use for "start the lab", "run system tests against the lab", or adding a new lab target.
---

# Retest lab

Local, intentionally vulnerable targets for safely developing and testing `revalid`. **The only systems this project ever retests are the ones defined here** (plus anything Álvaro explicitly authorizes in writing).

## Current shape (M1)

- `lab/docker-compose.yml` defines **OWASP Juice Shop** pinned to `v17.1.1`, bound to `127.0.0.1:3000` only. (DVWA and more targets are added per requirement.)
- `make lab-up` starts the container and polls `http://localhost:3000/rest/admin/application-version` until it answers; `make lab-down` stops and removes it.
- `make test-system` runs the `system`-marked tests; they wait for the lab and **skip** if it is unreachable (so the unit/integration suite stays green without Docker). `system-tests.yml` (nightly) brings the lab up before running them.
- `make demo-walking-skeleton` runs the full ingest→probe→verdict slice against the lab.
- Synthetic pentest reports describing known vulnerabilities live in `tests/data/` — they are the system-test/demo inputs (`juice_shop_login_sqli.json` drives the M1 login-bypass retest).

## Adding a target

1. Add the service to `lab/docker-compose.yml` (pin the image tag — system-test ground truth depends on the version). Bind to `127.0.0.1` only.
2. Add its base URL to the allowlist source if it differs from the default (`http://localhost:3000/*`); the retest engine refuses any target not allowlisted (FR-06).
3. Add a synthetic findings fixture under `tests/data/` and a `system`-marked test asserting the expected verdict.

## Safety rules (always in force)

- Lab binds to localhost only. Never expose lab ports beyond the host.
- The retest engine must refuse any target not in its authorization allowlist; the lab compose file is the default allowlist source.
- No destructive payloads in retest checks: verification probes only (presence/absence of vulnerability), never exploitation for impact.
- If a requirement ever calls for testing against non-lab systems, stop and require written authorization recorded as an ADR before implementing anything.
