---
name: run
description: Launch revalid locally so the full tool (React SPA + FastAPI /api backend) is operable in the browser. Use for "start the project", "run the app", "serve it locally", "check the frontend/backend", or driving the FR-11 end-to-end flow.
---

# Run revalid locally

The whole tool runs from **one uvicorn process** (ADR-0002): FastAPI serves the
JSON API under `/api` and the built React SPA at `/`. It binds to `127.0.0.1`
only — never expose it (NFR-03); there is no auth in TFG scope.

## Fastest full-stack run (recommended)

```bash
make build-ui   # builds frontend/dist — REQUIRED or `/` is API-only (see below)
make run        # uvicorn on http://127.0.0.1:8000, serves SPA + /api
```

- `make build-ui` runs `npm ci` + `vite build`. If `node_modules` is already
  present and you just want a fast rebuild, skip the clean install:
  `npm --prefix frontend run build` (typecheck + build, ~1s).
- **The SPA at `/` is only mounted when `frontend/dist/index.html` exists**
  (`_mount_spa` in `src/revalid/app.py` bails otherwise). Without a build you
  get a working `/api` but `/` returns 404. Always `build-ui` before `run` if
  you want to check the frontend.
- `make run` runs in the foreground. To keep it alive while you work, launch it
  in the background and tee its log:
  `make run > /tmp/revalid-server.log 2>&1 &`

Open **http://127.0.0.1:8000** — that single URL is both the frontend and,
under `/api`, the backend.

## Frontend dev with hot reload (two processes)

For iterating on the SPA (live reload, no rebuild per change):

```bash
make run       # terminal 1: backend on :8000
make dev-ui    # terminal 2: Vite dev server (proxies /api -> 127.0.0.1:8000)
```

Vite serves the SPA on its own port and proxies `/api` to the backend
(`frontend/vite.config.ts`), so relative `/api/...` paths work same-origin.

## Smoke tests (verified working)

```bash
curl -s http://127.0.0.1:8000/api/health        # -> {"status":"ok","version":"0.1.0"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/   # -> 200, SPA (title "revalid")
curl -s http://127.0.0.1:8000/api/findings       # -> []  (empty until something is ingested)
curl -s http://127.0.0.1:8000/api/reports        # -> []
```

- Interactive API docs (Swagger UI): **http://127.0.0.1:8000/docs**.
- Health returns the package `__version__`; the API prefix is `/api`, so the
  OpenAPI schema lives at `/openapi.json` / `/docs`, not under `/api`.

## Live-retest prerequisites (only for the full flow)

Uploading a PDF, extracting findings, and running a real retest needs:

- **Lab targets up**: `make lab-up`, plus `make sandbox-image` for the agent's
  toolbox (see the `retest-lab` skill). Retests reach only what is attached to
  the session's internal Docker network — in practice the lab container alone
  (FR-06 is network membership, not a target allowlist).
- **An LLM backend** for extraction/planning: either `ANTHROPIC_API_KEY` set,
  or `REVALID_LLM_MODEL=ollama:<model>` with a local Ollama server running.
  Without one, the extract/plan demos fall back to an offline stand-in model.
  Since ADR-0021 these env vars only **seed a fresh DB** on first run — they no
  longer override an already-configured setting. To change model, provider, or
  server address at runtime (no restart), use the SPA `/settings` view instead.
  Note on in-place upgrade: an existing install that relied on the old
  Claude default (via `ANTHROPIC_API_KEY`, no `REVALID_LLM_MODEL`) will seed the
  new local-first `ollama:qwen3.6:27b` default on first startup after upgrade —
  set the backend in `/settings` (or export `REVALID_LLM_MODEL` before that
  first run) to keep Claude.

`make demo-ui` = `build-ui` + `run` is the FR-11 acceptance target: the full
upload → approve plan → retest → verdicts flow, operable from the browser alone.

## Backend-only / offline demos

Individual feature slices run without the UI (see `Makefile`): `demo-ingest`
(FR-02), `demo-ingest-pdf` (FR-01), `demo-extract` (FR-03), `demo-plan`
(FR-04), `demo-approval` (FR-05), `demo-walking-skeleton` (M1, needs the lab).

## State / cleanup

- The app opens a SQLite file `revalid.db` in the working directory
  (`create_app(db_path="revalid.db")`). Delete it to reset persisted
  reports/findings/plans/verdicts.
- Stop the server with Ctrl-C (foreground) or by killing the background job.
