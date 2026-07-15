# 0021. User-configurable model/provider setting: DB-persisted, runtime-switchable, env-seeded

Date: 2026-07-15
Status: proposed

## Context

FR-13 / ADR-0010 made the LLM backend model-agnostic through a single environment
variable, `REVALID_LLM_MODEL` (a Pydantic AI `provider:model` string), with the
Ollama base URL supplied out-of-band via the provider's own `OLLAMA_BASE_URL`.
Switching backends is configuration-only, but it is **boot-time only**: the value
is read from `os.environ` at each agent build and cannot be changed while the app
runs.

The human-in-the-loop direction ratified this session (see ADR-0019/0020) makes the
choice of model a **product control the operator should own at runtime**, not a
deployment detail. During live testing the working backend is a local Ollama model
(`ollama:qwen3.6:27b`); Álvaro wants to change model, provider, and server address
from the running tool — for example to compare a smaller model, or to point at a
different Ollama host — without editing an env var and restarting.

Forces:

- The setting must **survive restarts** and take effect **without a restart**.
- Today `REVALID_LLM_MODEL` *always wins*; a runtime UI control would be inert while
  the documented run command exports that variable. Precedence has to change.
- Ollama needs a base URL, and ADR-0010 deliberately ships **no** default server
  address — so a "provider setting" that is only a model string cannot fully switch
  provider (e.g. to a different host) at runtime.
- API keys are secrets. The single-user, local threat model (ADR-0008) trusts the
  operator and keeps `revalid.db` gitignored, which widens what may be stored at rest.

## Decision

Add a **DB-persisted, runtime-editable model/provider setting** that becomes the
source of truth for backend selection, seeded from the environment on first run.

- **Source of truth & precedence — DB-primary, env seeds.** A single-row `settings`
  table holds the current `{model, base_url, api_key}`. On a fresh database it is
  **seeded once** from `REVALID_LLM_MODEL` / `OLLAMA_BASE_URL` if present, else from
  the new default; thereafter the stored row is authoritative and `os.environ` no
  longer overrides it. Backend selection reads the row, so a saved change takes
  effect on the **next** agent build — no restart. (The run docs are updated to say
  the env vars seed a fresh DB rather than override a configured one.)
- **Setting fields — `{model, base_url, api_key}` in the DB.** This fully configures
  a provider from the UI, including pointing at a different Ollama host. Consistent
  with ADR-0008, the API key is stored in the (gitignored) SQLite file. The key is
  **write-only across the API**: `GET /api/settings` returns a masked indicator
  (`api_key_set` + last-4), never the secret, so it is not echoed to the browser or
  logs.
- **Local-first default.** The shipped default becomes `ollama:qwen3.6:27b` at
  `http://localhost:11434/v1` (previously `anthropic:claude-sonnet-5`), matching the
  local, privacy-preserving, human-in-the-loop direction. A fresh checkout with no
  Ollama reachable fails on first *LLM* use with a clear error, but manual report
  entry (ADR-0020) works with no LLM at all, so the tool is still usable.
- **Resolution seam.** A new `settings.py` owns `load_or_seed(session)` and `save(...)`;
  `llm.py` gains `build_model(cfg)` that constructs the concrete Pydantic AI model —
  an explicit `OpenAIProvider(base_url, api_key)` when a base URL is set (covering
  Ollama and any OpenAI-compatible host), otherwise the native `provider:model` with
  the stored key if present, falling back to the bare string (env-supplied key). The
  composition root moves up into the agent DI factories (`get_extraction_agent`,
  `get_plan_agent`) and the ingest background task, which now `load_or_seed` and
  `build_model` before building via the existing `build_*_agent(model=…)` injection
  seam. Test overrides (which replace the whole DI function) are unaffected.
- **API.** `GET /api/settings` (masked) · `PUT /api/settings` (save is **not** gated
  on a successful probe) · `POST /api/settings/probe {base_url, api_key?}` which hits
  `{base_url}/models` and returns `{reachable, models, error?}`. The probe powers
  **both** the model dropdown and a Test-connection button — discovery *is* the test.
  For a native provider with no list endpoint (e.g. Anthropic) the probe reports
  "discovery unsupported — save and it validates on first use." The probe deliberately
  **bypasses the FR-06 allowlist**: the LLM host is infrastructure, not a pentest
  target, and under ADR-0008 the operator sets their own base URL, so no SSRF trust
  boundary is crossed.
- **Frontend.** A `/settings` route (Sidebar gear link) with a model field backed by
  the probe-populated dropdown, a base-URL field, a write-only key field
  (`set ••1234` / Change), a Test-connection button, and Save.

This is scoped as an **enhancement of FR-13** (model-agnostic config gains
"user-switchable at runtime, persisted"), not a new SRS requirement; this ADR is the
design of record.

## Alternatives considered

- **Env overrides DB (keep ADR-0010 precedence).** The env var, when set, wins and
  the UI edits only a fallback used when it is unset. Rejected: under the documented
  run command (which exports `REVALID_LLM_MODEL`) the runtime control would be inert,
  contradicting the goal.
- **Settings file on disk (JSON/TOML).** Precedence file > env > default, no DB
  migration. Rejected: introduces a config-file concept the project does not have and
  splits state between the DB and a file, when the DB is already the single store.
- **Model string only (base URL stays in `OLLAMA_BASE_URL`).** Simplest, minimal
  change to agent construction. Rejected: cannot switch to a different provider/host
  from the UI — only the model name — so it is half a provider setting.
- **Do not persist the API key (env-only secret).** The safer default in general.
  Rejected here by Álvaro: under ADR-0008 the operator is trusted and the DB is
  local/gitignored, and a fully UI-driven switch is worth more than keeping the key
  out of the SQLite file; the write-only API masking contains the exposure.
- **Validate-before-save (reject if provider unreachable).** Rejected: blocks saving
  a valid config while the server is momentarily down; the Test button gives feedback
  without coupling it to save.
- **Keep `anthropic:claude-sonnet-5` as the code default.** Rejected: contradicts the
  stated local-first default and would need an API key to work out of the box.
- **New FR-16 for the setting.** Rejected (Álvaro): it is a natural extension of
  FR-13, recorded as an added acceptance criterion rather than a new requirement
  number.

## Consequences

- **Easier:** the operator changes model, provider, and server address from the
  running tool, with model discovery and a connection test; the choice persists and
  applies without a restart. The tool is local-first by default.
- **Harder / accepted:** precedence changes — `REVALID_LLM_MODEL` now *seeds* a fresh
  DB instead of always overriding, which must be documented so a stale env var is not
  mistaken for the live setting. The API key is stored at rest in `revalid.db`
  (mitigated by gitignore + write-only API masking + the ADR-0008 threat model).
  `build_model` adds provider-construction logic beyond a bare model string; the exact
  Pydantic AI constructors are verified against upstream docs during implementation.
- **Runtime semantics:** a change takes effect on the next agent build; an in-flight
  ingest/retest keeps the model it started with. Acceptable — jobs are short and
  lineage records the model actually used (NFR-02, via `ReportRecord.model`).
- **Schema:** a new `settings` table is added to the existing `create_all` metadata,
  consistent with the project's "`rm revalid.db` on schema change" development
  practice (no formal migrations yet).
- **Status `proposed`:** the precedence change, the local-first default, and storing
  the key in the DB are Álvaro's to ratify.
