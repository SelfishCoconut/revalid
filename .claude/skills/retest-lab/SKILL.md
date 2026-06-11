---
name: retest-lab
description: Bring up / tear down the local vulnerable lab targets (docker compose) used for developing and system-testing the retest engine. Use for "start the lab", "run system tests against the lab", or adding a new lab target.
---

# Retest lab (STUB — expanded when the retest engine lands)

Local, intentionally vulnerable targets for safely developing and testing `revalid`. **The only systems this project ever retests are the ones defined here** (plus anything Álvaro explicitly authorizes in writing).

## Planned shape

- `lab/docker-compose.yml` defining: OWASP Juice Shop, DVWA (more targets added per requirement).
- `make lab-up` / `make lab-down`; system tests (`make test-system`) assume the lab is up and use ground-truth expectations per target version.
- Synthetic pentest reports describing known vulnerabilities of these targets live in `tests/data/` — they are the system-test inputs.

## Safety rules (always in force)

- Lab binds to localhost only. Never expose lab ports beyond the host.
- The retest engine must refuse any target not in its authorization allowlist; the lab compose file is the default allowlist source.
- No destructive payloads in retest checks: verification probes only (presence/absence of vulnerability), never exploitation for impact.
- If a requirement ever calls for testing against non-lab systems, stop and require written authorization recorded as an ADR before implementing anything.
