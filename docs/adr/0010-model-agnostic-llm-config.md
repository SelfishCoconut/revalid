# 0010. Model-agnostic LLM config: `REVALID_LLM_MODEL` env var, Ollama via Pydantic AI

Date: 2026-07-13
Status: proposed

## Context

FR-13 requires the LLM layer to be model-agnostic: Claude as primary
(ADR-0002), a local Ollama model configurable as fallback and as a comparison
condition for the FR-15 evaluation. The acceptance criterion is that switching
backends is **configuration-only** — no code change — and that both backends
run the same extraction suite. ADR-0009 already left the seam: the extraction
agent takes an injectable model and is built with `defer_model_check=True`, so
the only open question is where the model choice comes from and how the Ollama
backend is reached.

Constraints: this is a single-user local tool (ADR-0008) with no existing
settings framework — configuration so far is plain `REVALID_*` environment
variables (`REVALID_ALLOWLIST`, `REVALID_LAB_BASE_URL`).

## Decision

We will select the LLM backend from a single environment variable,
**`REVALID_LLM_MODEL`**, holding a Pydantic AI model string (provider-prefixed,
e.g. `anthropic:claude-sonnet-5`, `ollama:llama3.2`), defaulting to Claude
(`DEFAULT_MODEL`) when unset.

- A new `src/revalid/llm.py` owns the seam: `DEFAULT_MODEL` moves there and
  `resolve_model()` returns the configured model string. Anything that builds
  an agent without an explicit model gets the configured one, so the demo, the
  future API path, and the FR-15 harness all follow the same switch.
- **Ollama runs through Pydantic AI's native `ollama:` provider** (installed
  via the `openai` extra — Ollama speaks the OpenAI-compatible API). The server
  address comes from the provider's own `OLLAMA_BASE_URL` variable; we add no
  wrapper of our own. There is deliberately **no default base URL**: pointing
  at Ollama without saying where it runs is an error, not a guess.
- Backend reachability/credentials are checked at **first model call**, not at
  agent construction (`defer_model_check=True` stays) — construction remains
  offline-safe for tests and demos.

## Alternatives considered

- **A settings file (TOML/YAML) or `pydantic-settings`.** Rejected: the repo
  has exactly three knobs, all env vars; a config file adds a parser, a schema
  and documentation for one value. Revisit if config grows past a handful.
- **Pydantic AI `FallbackModel` (automatic Claude→Ollama failover).**
  Rejected for now: FR-13's "fallback" is an *operator choice*, and the FR-15
  evaluation needs the backend to be a controlled variable — silent runtime
  failover would blur which model produced which verdict lineage (NFR-02).
  A future ADR can layer `FallbackModel` on this seam if wanted.
- **A hand-rolled `OpenAIChatModel` + base-URL wiring for Ollama.** Rejected:
  Pydantic AI ships a first-class `ollama:` provider; wrapping it would be
  duplication (the #1 failure mode we guard against).
- **CLI flags per script.** Rejected: every entry point would re-implement the
  same flag; an env var is one switch shared by all of them and by CI.

## Consequences

- **Easier:** backend switching is `REVALID_LLM_MODEL=ollama:llama3.2` plus
  `OLLAMA_BASE_URL` — nothing else; the FR-15 comparison condition is a
  one-line environment change; any provider Pydantic AI knows works without
  code changes (`google:…`, `openai:…`, …), not just the two named ones.
- **Harder / accepted debt:** the model string is validated lazily (a typo
  surfaces at first call, not at startup); the `openai` extra (~openai SDK +
  tiktoken) joins the runtime dependencies even for Claude-only installs.
- Test obligation: the extraction suite stays model-injected (unchanged), and
  FR-13 adds tests that the env var alone re-targets the pipeline, plus an
  opt-in live-Ollama system test that skips when no server is reachable.
