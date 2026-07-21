# 0036. Reports chat assistant: a read-only tool-using agent over the corpus, with persisted threads

Date: 2026-07-20

Status: accepted
Introduces FR-18. Reuses the FR-13 backend selection (ADR-0010/0021) and the
Pydantic AI agent + gated/ungated tool pattern established by FR-17 (ADR-0025).

## Context

The tool answers per-finding questions well (extract → goal → retest → verdict),
but there was no way to ask questions *across* the whole corpus — "how many
reports do we have?", "how many findings relate to SQL injection?", "which report
has the most criticals?". Álvaro asked for a **Chat** tab in the left nav: a normal
chat with an agent that can see all the reports and answer such questions.

Three forces shape the design:

- **Exactness over vibes.** The value of the feature is *correct* counts. A model
  asked "how many findings relate to X" must not estimate from a prose dump.
- **Local models are small.** The default backend is a local Ollama model
  (`ollama:qwen3.5:9b`); stuffing the entire corpus into the prompt overflows its
  context and is exactly the failure the roadmap already recorded for report
  ingestion (ADR-0020's manual-entry escape hatch).
- **It is a normal chat.** The operator expects a multi-turn conversation that is
  still there after a page reload, like any chat tool.

The design forks were put to Álvaro (three choices, below); this ADR records what
he chose.

## Decision

**Ship FR-18 as a read-only, tool-using agent with persisted threads.**

- **Read-only query tools, not context-stuffing.** A new `reports_chat.py` holds a
  Pydantic AI agent (`build_reports_agent`) whose deps carry a DB `Session` and
  whose four **read-only** tools pull exactly what a question needs:
  `get_corpus_overview` (report/finding/verdict counts + breakdowns),
  `search_findings` (keyword/severity/report filter returning an **exact `total`**
  even when the row list is capped at 50), `list_all_reports`, and
  `finding_detail`. The tools are thin wrappers over plain, session-taking query
  functions that are unit-tested without any LLM. The agent mutates nothing and
  exposes no way to start a retest.
- **Persisted threads, not ephemeral.** New `chat_sessions` + `chat_messages`
  tables (SQLAlchemy, created by `create_all`; no migration needed — ADR-0002/0008).
  Only the prose turns (role + content) are stored — the agent re-queries the DB
  via its tools every turn, so persisting tool-call parts would be redundant.
  Prior turns are rebuilt into Pydantic AI `message_history` (`ModelRequest`/
  `ModelResponse`) on each call.
- **Reuse the FR-13 backend.** The agent is built from the persisted setting via
  `build_model` (a DI `get_reports_agent`), so it honours the same model/provider
  the rest of the tool uses — no separate configuration.
- **Inline request, not a background task.** `POST /api/chats/{id}/messages` runs
  the agent inline (a sync path operation on Starlette's threadpool) and returns
  the whole updated thread. The answer *is* what the caller awaits, unlike an
  FR-17 retest session (long-lived, streamed over a WebSocket). CRUD lives at
  `POST/GET /api/chats`, `GET/DELETE /api/chats/{id}`.
- **New requirement + tab.** It is a distinct capability, so it gets FR-18, its own
  issue (#136), and a left-nav **Chat** tab + `Chat.tsx` SPA view.

## Alternatives considered

- **Stuff a corpus summary into the prompt and use a plain prose agent** (like
  FR-17's `answer_operator_question`). Rejected: overflows the small local default
  on any real corpus and invites hallucinated counts — it fails the one thing the
  feature exists to do (exactness). Tools give the model precise, bounded data.
- **Ephemeral, client-held threads** (no DB change). Considered and put to Álvaro;
  he chose persistence so a conversation survives reload, consistent with the rest
  of the app storing everything. The cost is two small tables, accepted.
- **Fold it into FR-11** as an SPA enhancement (reuse `req:FR-11`, no ADR).
  Rejected by Álvaro's scope call: a new agent + endpoint + capability warrants its
  own requirement and traceability.
- **Give the agent write/act tools** (e.g. start a retest from chat). Out of scope:
  the request is analytics/Q&A; keeping it strictly read-only removes a whole class
  of risk and keeps the surface small.

## Consequences

- **Exact answers that scale.** The agent reads only what it needs, so counts are
  correct and the corpus size does not blow the context window — the same reason
  the tool-per-finding flow works on local models.
- **New persistent surface.** Two tables and a small REST surface are added; delete
  cascades by hand (`chat_messages` → `chat_session`), consistent with the app's
  no-FK-cascade SQLite (ADR-0002). Deleting a *report* does not touch chats — a
  thread is a free-standing conversation, not derived data.
- **Latency is visible.** An inline turn holds the request for the whole LLM call,
  which on the heavy local default can be tens of seconds; acceptable for a
  single-user localhost tool (ADR-0008), and the SPA shows a "thinking" indicator.
  If this becomes annoying, the turn can move to the FR-17 background+stream shape
  later without changing the data model.
- **Read-only by construction.** No chat tool can mutate data or launch a retest,
  so the feature adds no new authority to the agent beyond reading the DB.
- **NFR-05 held.** mypy strict, ruff, xenon ≤ C, coverage ≥ 80% on `src/`, and the
  frontend eslint/tsc/vitest gates all stay green.
