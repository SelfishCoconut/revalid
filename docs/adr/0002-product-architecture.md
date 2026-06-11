# 0002. Product architecture: FastAPI + React SPA, SQLite, Claude-primary LLM, plan-approve-execute with sanity checker

Date: 2026-06-11
Status: accepted

## Context

The requirements elicitation interview (2026-06-11, recorded in `docs/requirements/srs.md`)
required Álvaro to fix the product's architectural cornerstones: interface, persistence,
LLM strategy, and the execution-safety model for a tool that turns pentest-report content
into executed probes.

## Decision

Álvaro decided:

- **Interface**: local single-user **web application** — FastAPI backend + **React SPA** frontend, bound to 127.0.0.1, no authentication in TFG scope (FR-11, NFR-03).
- **Persistence**: **SQLite** via SQLAlchemy — zero-ops single file; findings, plans, runs, and the full audit trail (FR-10) live there.
- **LLM layer**: **Pydantic AI**, model-agnostic; **Claude API primary**, local model (Ollama) as configurable fallback and evaluation comparison (FR-13).
- **Execution model**: **plan → human approve → execute**, with an independent **execution sanity checker** (FR-08) that blocks plan deviation and forces *inconclusive* on ambiguity (patched vs endpoint-changed) instead of letting the model guess.
- **Probes**: HTTP-level first (httpx); browser probes (Playwright) deferred to Could (FR-14).

## Alternatives considered

- **CLI as primary interface** — recommended by the assistant for scope economy; rejected by Álvaro: the approval workflow and evidence drill-down are first-class UI concerns, and a web deliverable is the desired product shape.
- **HTMX/server-rendered UI** — rejected in favor of React SPA: richer interactivity for plan editing and evidence views; accepted cost: second toolchain (TypeScript) in CI.
- **PostgreSQL** — rejected: service dependency in every environment with no single-user benefit.
- **Fully autonomous execution on allowlist** — rejected: weaker safety story (IS5) and weaker authorship/control narrative; an explicit auto-approve mode for lab targets may be added later without changing the architecture.

## Consequences

- A TypeScript/React workstream joins the project: frontend linting, tests, and build enter CI when the SPA lands; the `frontend-design` plugin supports it.
- The API must be designed contract-first so the SPA and the evaluation harness (FR-15) consume the same endpoints/exports.
- SQLite keeps tests hermetic (one temp file per test); a Postgres migration path is documented future work.
- The sanity checker doubles as a thesis contribution: a concrete mechanism against LLM overconfidence in security verdicts.
