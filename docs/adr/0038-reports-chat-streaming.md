# 0038. Reports chat streams its reply token-by-token over SSE, which forces an async endpoint

Date: 2026-07-21

Status: accepted (ratified 2026-07-25)

Enhances FR-18 (ADR-0036). Realises the evolution ADR-0036 explicitly left open:
its *Consequences* noted "latency is visible … if this becomes annoying, the turn
can move to the FR-17 background+stream shape later without changing the data
model."

## Context

FR-18 shipped with an **inline, blocking** turn: `POST /api/chats/{id}/messages`
runs the whole agent turn and returns the complete thread in one response, so the
operator stares at a "thinking" indicator for the entire LLM call. On the default
heavy local backend (`ollama:qwen3.5:9b` and larger) that is tens of seconds of
apparent freeze before anything appears. Álvaro's request: "make the agent chat
stream the answer so I can see in real time what's replying."

Pydantic AI can stream a plain-prose (`output_type=str`) turn as text deltas, and
the FR-13 backends (Anthropic, OpenAI-compatible, Ollama) all support it, so there
is no model-side blocker. The design questions are the *transport* and the
*execution model*.

## Decision

**Add a streaming endpoint that emits the reply as Server-Sent Events, and make it
async so the stream runs in the request's own event loop.**

- **Transport: SSE over the existing POST, not a WebSocket.** A new
  `POST /api/chats/{id}/messages/stream` returns a `text/event-stream` response:
  one `event: token` frame per text delta (`data: {"text": "…"}`), a terminal
  `event: done`, and `event: error` if generation raises. Chat is a
  request→response exchange (a question in the POST body, one growing answer out),
  not a long-lived bidirectional job, so SSE fits where the FR-17 retest console's
  WebSocket would be overkill. The blocking `…/messages` endpoint is **kept** as a
  fallback and for the existing tests.
- **Execution: async `run_stream`, not sync `run_stream_sync`.** The endpoint and
  the generator (`reports_chat.stream_answer`) are `async`. The sync bridge
  (`run_stream_sync`) binds its worker to the calling thread via an anyio portal;
  when FastAPI's `StreamingResponse` iterates a *sync* generator across threadpool
  threads, the portal's cancel scope is exited on the wrong task and the stream
  dies with `RuntimeError: Attempted to exit a cancel scope that isn't the current
  task's`. Running `agent.run_stream` inside an `async def` generator keeps the
  whole stream in the request's event loop and removes the thread-bouncing.
- **Persistence unchanged.** `stream_answer` records the user turn (and the thread
  title on the first message) before generating, yields each delta, and appends the
  completed assistant turn once the stream drains — identical to `answer_question`,
  so a streamed thread and a blocking thread are stored the same way. The generator
  opens its **own** DB session (the request-scoped one closes when the endpoint
  returns, before the body is consumed).
- **Client.** The SPA reads the SSE body incrementally (`fetch` + `getReader`),
  grows the assistant bubble per token, and — on `done` — refetches the persisted
  thread and hands off from the live text to the stored message with no flicker.

## Alternatives considered

- **Keep the blocking turn.** Rejected: it is the exact problem — the answer is
  invisible until the whole (slow, local) call finishes.
- **Stream over a WebSocket, like the FR-17 retest console.** Rejected: a WS is for
  a long-lived, bidirectional session with its own event stream; chat is a single
  request/response. `EventSource` is GET-only (no POST body), so a `fetch`-read SSE
  stream is the right same-origin fit and reuses the existing JSON client style.
- **Keep the endpoint sync and use `run_stream_sync`.** Rejected after it failed
  live: the thread-affine portal breaks inside `StreamingResponse` (the cancel-scope
  error above). Going async is the fix, not a workaround.
- **Replace the blocking endpoint outright.** Rejected: keeping it as a fallback
  costs nothing, preserves the existing integration tests, and leaves a
  non-streaming path for any client that wants the whole thread in one call.

## Consequences

- **The answer appears as it is generated.** The operator sees tokens immediately;
  the "thinking" dots show only until the first token. This is the visible-latency
  fix ADR-0036 anticipated, with no change to the chat data model.
- **First SSE in the codebase.** A small `_sse` framing helper and an async
  streaming generator are added; the pattern is now available for any future
  streamed endpoint. The FR-17 console keeps its WebSocket (different shape).
- **Async chat path.** The streaming endpoint is the app's first `async def` route
  that runs the LLM; its DB work is synchronous SQLAlchemy on the event loop, which
  is acceptable for a single-user localhost tool (ADR-0008).
- **Testing.** The streaming path is proven end-to-end by an integration test (real
  ASGI + SSE frames + persistence). It is deliberately **not** unit-tested with the
  real agent: driving the async generator needs an explicit event loop
  (`asyncio`/`anyio.run`) that competes with the background portal Pydantic AI
  reuses for the `run_sync`-based agent unit tests and intermittently corrupts the
  shared in-memory SQLite connection. `stream_answer`'s persistence + `(no answer)`
  fallback logic is identical to the unit-tested `answer_question`. Total `src/`
  coverage stays above the 80% floor.
- **NFR-05 held.** mypy `--strict`, ruff, xenon ≤ C, `src/` coverage ≥ 80%, and the
  frontend eslint/tsc/vitest gates all stay green.
