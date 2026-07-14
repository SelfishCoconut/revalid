# 0013. React SPA architecture: PDF-ingest background jobs, `/api` prefix, FastAPI-served single-page app

Date: 2026-07-14
Status: proposed

## Context

FR-11 requires that the full evaluation flow — ingest → approve → execute →
verdicts with evidence — be *operable from the UI alone*, from a React SPA that
FastAPI serves on localhost (ADR-0002 fixed the stack). Everything behind that
surface already exists as HTTP endpoints after FR-05 (ADR-0012) — plan generate /
edit / approve / reject / retest, verdicts with evidence — with **one gap**: there
is no way to get a real pentest **report** into the system over HTTP. The
FR-01/FR-03 pipeline (`read_pdf` → `extract_report`) that turns a PDF into
`Finding`s is reachable only from the CLI (`make demo-extract`); the sole ingest
endpoint is `POST /findings/import` (DefectDojo **JSON**).

Two structural forces constrain a browser client served from the same origin:

- **Route collision.** The API lives at the root (`/findings`, `/verdicts`,
  `/findings/{id}/…`). An SPA needs client-side routes (`/reports`, `/findings/5`)
  that clash with those paths — `/verdicts` is both a wanted SPA route and a real
  API route. One origin serving both forces this to be resolved.
- **Slow extraction.** LLM extraction takes ~30–80 s per report (roadmap: 8/8
  findings in 78 s on `ollama:qwen3.5:9b`). A blocking upload would freeze the UI
  for over a minute with no feedback and risk client/proxy timeouts.

FR-11 is the last M3 card; shipping it releases `v0.3.0`. The full design dialogue
is recorded in `docs/superpowers/specs/2026-07-14-fr11-react-spa-design.md`.

## Decision

We will build the SPA as a **Vite + React + TypeScript + Tailwind** app under
`frontend/`, served in production by FastAPI, and wire report ingestion into the
HTTP surface for the first time. Five decisions fix the shape:

- **Ingest is PDF upload, run as a background job the UI polls.** A new
  `POST /api/reports` (multipart PDF) inserts a `reports` row in `extracting`
  status, schedules a background task, and returns `202` immediately. The task —
  a sync function Starlette runs in its threadpool, holding its **own** `Session`
  — runs `read_pdf` → `extract_report`, persists the findings, and flips the row
  to `ready` (or `failed`, with the error, so a report is never stuck). The client
  polls `GET /api/reports/{id}` until it settles. This finally exposes FR-01/FR-03
  over HTTP, which until now were CLI-only.
- **The report is the job.** A single `reports` table carries both the uploaded
  file's identity and the job status (`extracting`/`ready`/`failed`, plus `model`
  lineage and `finding_count`) — no separate `jobs` table. `findings` gains a
  **nullable** `report_id`; JSON-imported findings keep `report_id = NULL`. This
  is also FR-11's "report/run overview" unit.
- **The API moves under `/api`; the SPA is served at `/` with a catch-all.** Every
  existing route is carried unchanged onto an `APIRouter(prefix="/api")`; the built
  `frontend/dist` is mounted at `/`, and unmatched non-`/api` GETs return
  `index.html` for client-side routing. When no build is present (backend-only dev,
  CI unit tests) the catch-all is simply absent and the API still works. The bind
  stays `127.0.0.1` (NFR-03) — static files open no new network surface.
- **An injectable extraction agent keeps ingest testable without a network.** A
  `get_extraction_agent` dependency (mirroring `get_plan_agent`, ADR-0012) lets
  tests substitute a Pydantic AI `TestModel`/`FunctionModel`. Because Starlette
  runs background tasks before `TestClient` returns, the job completes in-test with
  no polling or sleeping — the acceptance chain is exercised headlessly.
- **The client uses `react-router-dom` and `@tanstack/react-query`; types are
  hand-written.** Router gives clean drill-down URLs (reload-safe via the
  catch-all); react-query is a near-exact fit for poll-until-ready plus
  invalidate-on-approve. A hand-written typed API client mirrors the Pydantic
  models — ~10 endpoints do not justify an OpenAPI codegen step.

**Verification is right-sized (D6 of the spec).** CI gains a `frontend` job
(eslint + `tsc --noEmit` + `vite build` + `vitest` component tests) — fast, no
lab/LLM. The end-to-end "operable from the UI alone" criterion is a **manual**
Playwright walkthrough (`make demo-ui` + the PR's How-to-validate) against the live
lab, mirroring how the M1 system test is nightly-only rather than a PR gate.

**The history/"audit" drill-down shows only what exists today** — a finding's
plan-version history plus each verdict's evidence. The unified, re-derivable audit
trail is FR-10 (#15, M4); FR-11 surfaces the FR-05 version rows, it does not build
the M4 trail.

## Alternatives considered

- **JSON import as the UI's ingest path (no PDF endpoint).** Rejected: it would
  keep FR-01/FR-03 CLI-only and make "operate the whole tool from a real report"
  impossible in the UI — the weakest version of the thesis demo — to save a modest
  endpoint. The compelling story is upload-a-PDF.
- **Synchronous extraction with a spinner.** Rejected: a 30–80 s blocking request
  has no progress affordance and invites client/proxy timeouts. A background job +
  poll is barely more code (the report row already needed a status) and far better
  UX, without pulling in websockets.
- **A separate `jobs` table distinct from reports.** Rejected: the report and the
  ingest job have the same lifetime and the same identity; one row models both and
  doubles as FR-11's overview unit. Two tables would be duplication for no gain.
- **Keep the API at root; use a HashRouter (`/#/…`).** Rejected: it avoids the
  prefix churn but yields uglier URLs, a worse dev-proxy story, and a design that
  reads less cleanly in the thesis. The `/api` split is the conventional resolution
  and the churn (demo scripts + tests) is mechanical and one-time.
- **A component library (MUI/Chakra) or hand-rolled CSS instead of Tailwind.**
  Rejected: a component library is a heavy dependency with a strong visual identity
  hard to justify for a localhost tool; hand-rolled CSS is more to maintain.
  Tailwind is the middle path — fast, light, no imposed look.
- **OpenAPI-generated TypeScript client.** Rejected for now: it adds a generation
  step and a running-server/committed-schema dependency for ~10 endpoints.
  Hand-written types are simpler; revisit if the surface grows.
- **Full Playwright E2E in CI.** Rejected: it would force the lab + an LLM (or
  heavy mocking) into CI — slow and flaky — for a guarantee the manual walkthrough
  already gives. Consistent with ADR-0004 (ceremony scales with thesis value).

## Consequences

- **Easier:** the whole tool runs from one `uvicorn` process on localhost — no
  separate frontend server in the demo/eval path; FR-01/FR-03 are finally reachable
  over HTTP, so the evaluation can be driven end-to-end from a report; the `/api`
  split gives an unambiguous origin contract (API vs app) and a trivial dev proxy;
  the injectable agent means the ingest acceptance path is unit/integration-tested
  with no network; react-query removes hand-rolled polling/refetch bug surface.
- **Harder / accepted debt:** the codebase gains a **second toolchain** (Node/npm,
  eslint, vitest) and a CI job to maintain; hand-written TS types can drift from the
  Pydantic models (mitigated by a shape test, revisitable via codegen); a background
  job lost to a mid-run server restart leaves a report stuck in `extracting`
  (single-user, single-process — re-upload is trivial; no job queue, ADR-0008); the
  `/api` move is a one-time breaking change to every existing endpoint path,
  touching ~6 demo scripts and the test/system suites; the "audit" view is the
  minimal FR-05 version history until FR-10 (M4); the end-to-end acceptance is a
  manual demo, not an automated gate (D6).
- **Status `proposed`:** the PDF-ingest-as-background-job model, the `/api` prefix
  break, the report-is-the-job merge, and the manual-acceptance split are Álvaro's
  to ratify in async review, per the design spec cited above.
