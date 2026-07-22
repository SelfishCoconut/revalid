# Working on revalid — environment & process

!!! note "This page describes the environment as it is"
    It began life as the *plan* for the development environment (adopted in
    **[ADR-0001](adr/0001-development-environment-and-process.md)**, and cited as
    such by ADR-0006 and ADR-0008). The environment now exists, so the page
    documents what is actually in the repository. The original forward-looking
    plan is preserved in git history. Section numbers are stable — `Makefile`
    and `CLAUDE.md` reference §5 and §9.

## 1. Toolchain

| Tool | Used for |
|---|---|
| **Python 3.12+**, managed by **`uv`** | the backend; run everything via `uv run` or `make` |
| **ruff** | lint + format (line length 100, Google docstrings on public API) |
| **mypy `--strict`** | full type hints, no escape hatches |
| **pytest** (+ `pytest-cov`) | the three test levels of §6 |
| **xenon / radon / vulture / pylint / pydeps** | the mechanical sanity signals of §5 |
| **Node + npm** | the React SPA under `frontend/` (Vite, TypeScript, Tailwind, vitest) |
| **Docker** | the retest lab (`lab/docker-compose.yml`) and the FR-17 agent sandbox |
| **XeLaTeX + latexmk** | the thesis (`thesis/`, ESII template, Carlito font) |

Two optional extras keep heavy dependencies off the default install — HTTP and
unit paths never import them:

```bash
uv sync --extra sandbox    # docker SDK — FR-17 agentic retest sandbox (ADR-0025)
uv sync --extra browser    # playwright — FR-14 browser probes (ADR-0018)
```

!!! warning "`browser` is legacy"
    ADR-0033 retired the batch execution path and dropped FR-14. The `browser`
    extra is still declared in `pyproject.toml` but nothing on `main` imports it.

## 2. First-time setup

```bash
uv sync                     # backend + dev dependencies
uv run pre-commit install   # commit-msg (Conventional Commits) + format hooks
make ui-install             # reproducible SPA toolchain install (npm ci)
make lint typecheck test-unit
```

## 3. Running it

```bash
make lab-up                 # authorised targets (Juice Shop, pinned) — needed for a real retest
make run                    # build the SPA if needed, serve everything on 127.0.0.1:8000
make lab-down
```

`make dev-ui` runs the Vite dev server with hot reload (backend in another
shell); `make reset-db` drops a stale `revalid.db` when the schema has moved.
Seed data deterministically with manual ingestion rather than a PDF upload — see
the [workflow page](architecture/workflow.md).

Every feature ships something runnable: `make demo-ingest`, `demo-ingest-pdf`,
`demo-extract`, `demo-retest-session`, `demo-audit`, `demo-export`, `demo-eval`,
`demo-settings`, `demo-ui`. Most run fully offline with a Pydantic AI stand-in
when no model is configured.

## 4. Repository layout

```
src/revalid/     backend package (see the API reference for per-module docs)
frontend/        React SPA (Vite + TS + Tailwind); built into frontend/dist
tests/           unit / integration / system — see §6
scripts/demo/    one runnable demo per feature, referenced from PR bodies
lab/             docker compose for the intentionally vulnerable targets
docs/            this site (docs-as-code, §8)
thesis/          the memoir (XeLaTeX, ESII template)
.claude/         skills, agents and hooks that automate the process below
```

## 5. Quality gates and sanity metrics

Enforced on every change, no suppressions:

- **`make lint`** — ruff lint + format check.
- **`make typecheck`** — `mypy --strict` over `src/` *and* `tests/`.
- **Complexity gate** — `xenon --max-absolute C`. A function that trips it gets
  refactored, never silenced.
- **Coverage** — ≥ 80 % on `src/` (`fail_under = 80`), measured by the unit run.

**`make sanity`** emits the mechanical signals — cyclomatic complexity,
maintainability index, dead code, duplicate detection, dependency graph — that
the **`codebase-sanity`** agent consumes. That agent performs a longitudinal
audit for the failure modes AI-assisted development actually produces:
duplication, dead code, complexity creep, pattern inconsistency, architectural
drift, test-health erosion. Run it before every milestone release.

Two further agents guard the same boundary: **`doc-curator`** (docstring
coverage, authored-diagram drift, missing ADRs) on PRs, and
**`thesis-reviewer`** against the ESII tribunal rubric.

## 6. Testing (the pyramid)

| Level | Marker | Contract |
|---|---|---|
| `tests/unit/` | — | no I/O. LLM code uses Pydantic AI `TestModel`/`FunctionModel`; Docker is replaced by `FakeSandbox`. Coverage is measured here. |
| `tests/integration/` | `integration` | real component wiring and real I/O, fakes only at the outermost edge. Still no API key, still deterministic. |
| `tests/system/` | `system` | the full flow against the dockerized lab. Heavy — nightly and on demand, not per PR. |

```bash
make test-unit          # fast, every change
make test-integration
make test-system        # needs `make lab-up` + the sandbox extra
make test               # all three
```

Because the LLM is never real in CI, the whole HTTP flow is exercisable with no
network and no Docker daemon. Each FR in the [SRS](requirements/srs.md) carries
acceptance criteria backed by a test, feeding the traceability matrix.

## 7. Continuous integration

`ci.yml` runs four jobs per change — **Lint & types**, **Unit tests +
coverage**, **Integration tests**, **Frontend (lint, types, build, tests)** —
alongside `security.yml` (**pip-audit**, **Bandit**, **Gitleaks**) and
**CodeQL**. Since ADR-0003's 2026-07-21 update, *all* per-change jobs are
**required**: the gate is load-bearing precisely because no human reviews before
merge. Scanner false positives block; the standing policy is to fix the code or
record a justified dismissal, never to lower the threshold.

Scheduled workflows: `system-tests.yml` (nightly, lab), `sanity.yml` (metrics),
`docs.yml` (deploys this site to GitHub Pages on every push to `main`),
`thesis.yml` (builds the PDF artifact), `board.yml` (Kanban automation).

## 8. Documentation system (docs-as-code)

Anything derivable from code is **generated at build time**, so it cannot drift:

- **API reference** — mkdocstrings from Google-style docstrings and type hints.
- **UML class & package diagrams** — `pyreverse` via `scripts/gen_uml.py`,
  regenerated on every `make docs` into `docs/reference/generated/` (never
  hand-edited). One diagram per group of modules rather than one unreadable
  whole-package dump; a module missing from the script's `LAYERS` fails the build.
- **Authored** — the C4 model, sequence diagrams and narrative pages, written as
  Mermaid inside markdown so GitHub renders them and PRs diff them. A change to
  code they describe must update them in the same PR (`doc-curator` checks).

```bash
make uml          # regenerate the UML diagrams only
make docs         # regenerate UML, then mkdocs build --strict
make docs-serve   # live preview
```

`mkdocs build --strict` failing on a broken link is a CI failure — fix it.

## 9. Process — Kanban adapted to solo dev + AI

AI-assisted solo development moves too fast for fixed-length sprints: cycle time
per feature is hours, not weeks. The process is therefore **continuous flow** on
a GitHub Projects board, automated by `board.yml`.

- **Issue first — non-negotiable.** Every change starts as a GitHub issue
  (`feature-request` skill, or `gh issue create` with a `req:FR-xx` / `infra` /
  `thesis` label + milestone). Enhancements to an existing FR reuse its label.
  A PreToolUse hook reminds on branch/PR creation.
- **Flow**: card → feature branch → PR → **`Verify`** (required CI green +
  automated review) → auto-merge (squash) → **`Validate`** = Álvaro's async
  review, with full revert authority.
- **Auto-merge, not a manual pre-merge block** (ADR-0003). The PR body must
  contain `Closes #<n>` so `board.yml` advances the card.
- **"How to validate" is mandatory** in every PR: exact commands, expected
  output, acceptance criteria as checkboxes. If a feature isn't directly
  runnable, it ships `scripts/demo/<feature>.py` or `make demo-<feature>`.
- **Conventional Commits**, enforced by a commit-msg hook.
- **Definition of Done**: code + tests + docstrings + affected docs/diagrams
  updated + required CI green; Álvaro's async review may add follow-ups.
- **Milestones** mark feature-complete increments, not time boxes; each closes
  with a GitHub Release (SemVer + CHANGELOG).

Significant decisions become **ADRs** (`adr` skill, MADR format). A decision
without an ADR doesn't exist.

## 10. Authorship and AI governance

Reglamento TFG 2026 §6 is non-negotiable and is Álvaro's own responsibility:

- Álvaro makes all design decisions and reviews **all** AI output. Claude
  assists; it never decides scope or architecture unilaterally.
- Every AI-assisted commit carries a `Co-Authored-By: Claude` trailer.
- Sessions are auto-logged to `docs/ai-usage/sessions/` by a hook; the curated
  [AI usage log](ai-usage/AI_USAGE_LOG.md) keeps one entry per work session.
- The thesis AI declaration is generated by the `ai-declaration` skill and
  reviewed by Álvaro directly, not by an agent (ADR-0005).
- Test data in `tests/data/` is synthetic or lab-derived; `data/private/` is
  gitignored. There is no *enforced* data-policy hook — under the single-user
  threat model (ADR-0008) secret hygiene is direct human responsibility, not
  ceremony (ADR-0006).

## 11. Context efficiency

The repository is designed so Claude works from indexes, not file dumps: code
discovery goes through the **codebase-memory** graph
(`search_graph` / `trace_path` / `get_code_snippet`), re-indexed after
structural changes. `CLAUDE.md` stays short — rules and pointers — while
**skills** carry heavyweight knowledge and load on demand. Durable knowledge
lives in the SRS, ADRs and this site, never in conversation, so any session can
cold-start from files. Searching the graph for an existing implementation
**before** writing a new helper is mandatory: duplication is the number-one
AI-development failure mode here.
