# TFG Development Environment Setup

## Context

Álvaro's TFG (ESII-UCLM): **"AI-Driven System for the Revalidation of Pentest Findings"** — a tool that parses pentest reports, extracts findings and reproduction steps, autonomously re-executes them against the audited systems, and reports whether fixes are effective. Thesis in English, ESII XeLaTeX template, ≤80 recommended pages. Public GitHub repo with CI/CD.

This plan creates the **development environment** (repo scaffold, CLAUDE.md, hooks, skills, agents, CI/CD, plugins) and then, immediately after setup is verified, runs the **requirements elicitation interview** with Álvaro (§9). Actual code comes after, driven by the resulting SRS.

**Status note**: implementation was interrupted before anything was created — no repo, no hooks, no logger exist yet. Tooling check done: `git`, `gh` (authenticated), `uv`, `pre-commit` available; `xelatex`, `latexmk`, `gitleaks`, `ruff` must be installed during setup. The AI-session logger only records from the moment it is installed, so the design sessions held so far get seeded manually into `AI_USAGE_LOG.md` (step 9).

**Hard compliance constraints** (from `docs/ReglamentoNormativaTFG_2026.pdf`, Section 6 + Anexo I rubric):
1. The memoria must **declare AI tools used, the type of use, and the affected sections** (methodology or conclusions chapter).
2. Álvaro retains **full authorship responsibility**; work effectively authored by AI is not admissible → he designs/decides, AI assists; all AI output reviewed by him.
3. Copyright: all third-party content cited/licensed; original sections clearly differentiated.
4. **No personal or protected third-party data may be fed into AI tools** → only synthetic/lab pentest data ever enters the repo or Claude's context.

Decisions made with the user: **Python 3.12+ with uv** (Pydantic AI for the agent layer), **monorepo**, **fully automated AI-usage tracking**, **Beerware license** (user's explicit choice; note in README it's not OSI-approved, which is fine for a TFG).

## 1. Repository scaffold

`git init` in `/home/alvar/tfg`, create public GitHub repo via `gh repo create` (suggested name: `pentest-revalidator` — confirm with Álvaro at creation time).

```
tfg/
├── CLAUDE.md                      # source of truth (see §2)
├── README.md                      # project intro, AI-usage notice, thesis link
├── LICENSE                        # Beerware (Rev. 42), Álvaro Navarro
├── .gitignore                     # Python, LaTeX aux, .env, data/private/, docs/normativa PDFs
├── pyproject.toml                 # uv project; ruff, mypy, pytest, coverage config
├── mkdocs.yml                     # docs site (Material + mkdocstrings) — see §11
├── Makefile                       # lint / typecheck / test / thesis / all targets
├── .pre-commit-config.yaml        # ruff, ruff-format, gitleaks, hygiene hooks
├── .claude/
│   ├── settings.json              # hooks wiring, enabled plugins, permissions
│   ├── hooks/                     # shell scripts (see §3)
│   ├── skills/                    # project skills (see §4)
│   └── agents/                    # custom agents (see §5)
├── .github/
│   ├── workflows/                 # ci.yml, security.yml, thesis.yml (see §6)
│   ├── dependabot.yml
│   ├── ISSUE_TEMPLATE/            # feature (requirement), bug, thesis-task
│   └── PULL_REQUEST_TEMPLATE.md   # mandatory "How to validate" + self-review + AI-assistance checklist
├── src/                           # empty package skeleton (filled in next phase)
├── tests/                         # unit/, integration/, system/, data/ (synthetic reports) — see §8
├── thesis/                        # ESII English LaTeX template (copied from docs/)
└── docs/
    ├── adr/                       # MADR-format decision records + index
    ├── ai-usage/
    │   ├── AI_USAGE_LOG.md        # curated log (one entry per work session)
    │   └── sessions/              # auto-appended raw session records (hook)
    └── normativa/                 # PDFs stay local, .gitignored (link in README)
```

Notes:
- `data/private/` is the designated location for anything sensitive; gitignored **and** blocked from Claude's tools by hook (§3).
- The ESII template uses **Calibri via XeLaTeX/fontspec** — not available on Linux/CI. Use the metric-compatible **Carlito** font (package `ttf-carlito` locally, `fonts-crosextra-carlito` in CI) with a fontspec substitution in `include/configuracion.tex`.

## 2. CLAUDE.md (source of truth)

Concise rules file covering:
- **Project**: one-paragraph description + pointer to `docs/tfg_description`.
- **Roles & authorship** (regulation §6): Álvaro makes design decisions and reviews everything; Claude assists. Significant decisions → ADR in `docs/adr/`. Never merge unreviewed AI code.
- **Data protection**: never read, paste, or commit real pentest reports, credentials, or personal data. Only synthetic/lab data. `data/private/` is off-limits to tools.
- **Coding standards**: Python 3.12+, full type hints (mypy strict), ruff lint+format, pytest with coverage on core logic, Google-style docstrings on public API.
- **Workflow**: GitHub issue → feature branch → PR → CI green → Álvaro self-review checklist → squash merge. Conventional Commits. Claude's commits carry the `Co-Authored-By: Claude` trailer (this is the machine-readable AI marker).
- **Thesis**: English, ESII template, build via `make thesis` (latexmk -xelatex); keep AI usage log current; chapter style conventions.

## 3. Hooks (`.claude/settings.json` + `.claude/hooks/`)

| Hook | Event / matcher | Behavior |
|---|---|---|
| `protect-private-data.py` | PreToolUse on Read/Grep/Glob | **Deny** access to `data/private/**`, `*.env`, credential/key files. Retained as generic secret-file hygiene; the enforced §6 data policy it originally implemented was removed (ADR-0006). |
| `format-on-edit.sh` | PostToolUse on Edit/Write of `*.py` | `uv run ruff format` + `ruff check --fix` on the touched file. |
| `pre-commit-gate.sh` | PreToolUse on Bash matching `git commit` | Run `gitleaks protect --staged` + verify Conventional Commit message format; block on failure. |
| `log-ai-session.sh` | SessionEnd (and Stop as fallback) | Append a record to `docs/ai-usage/sessions/YYYY-MM.md`: timestamp, branch, `git diff --stat` of files touched, session id. Feeds the `/ai-declaration` skill. |

Keep hooks as small POSIX shell scripts; wire them in project `settings.json` so they travel with the repo.

## 4. Project skills (`.claude/skills/`)

- **`ai-declaration`** — compiles `docs/ai-usage/` + git history (commits with the Claude trailer, per-path stats) into the AI-usage declaration section of the thesis methodology chapter: tools used, type of use (code generation, review, writing assistance…), affected sections/modules. This is the regulation §6 deliverable.
- **`thesis`** — conventions + build for the memoria: academic-English style guide, template macros (`\listofcodes`, figure/table referencing rules from the Anexo I rubric), `make thesis` build, page-budget check (warn approaching 80 pages from chapter 1 to bibliography).
- **`adr`** — create a MADR-format ADR in `docs/adr/`, update index. Records the human decisions that demonstrate Álvaro's authorship.
- **`progress-report`** — on-demand summary (board movement, commits, closed issues, open questions since a given date) for whenever a status write-up is useful.
- **`retest-lab`** *(stub for next phase)* — bring up vulnerable lab targets (e.g. OWASP Juice Shop, DVWA) with docker compose for safely testing the retester. Only lab targets; never real systems without written authorization.
- **`requirements`** — scaffold/update FR-xx / NFR-xx entries in `docs/requirements/srs.md`, keep IDs and MoSCoW priorities consistent, and create/sync the matching `req:FR-xx` GitHub issues (see §10).
- **`docs`** — build/serve the MkDocs site, regenerate the pyreverse UML diagrams, and check authored diagrams for drift (see §11).

## 5. Custom agents (`.claude/agents/`)

Keep minimal — the code-review plugin already covers general review:
- **`security-auditor`** — reviews diffs for vulnerabilities (it's a security tool; injection into the retest executor is a real risk), checks no sensitive data is being committed, validates that retest actions stay within lab-target scope.
- **`thesis-reviewer`** — reviews chapter drafts against the tribunal's Anexo I rubric (structure, clear English, all figures/tables referenced and discussed, original sections differentiated, sources cited) and checks the AI declaration stays consistent with the log.
- **`doc-curator`** — owns documentation health. On each PR / on demand: checks that public APIs have docstrings (they feed the generated docs), that authored Mermaid diagrams (sequence, C4) still match the code they describe, that `docs/` pages affected by the diff are updated, and flags when a decision deserves an ADR. Complements the auto-generated docs in §11, which never need manual syncing.
- **`codebase-sanity`** — whole-repo, longitudinal quality guardian targeting the specific pathologies of AI-assisted development, which diff-scoped review cannot see (each PR looks fine; the codebase still degrades). Run on demand and **before every milestone release**; it interprets mechanical signals rather than just reading code:
  - *Duplication*: near-duplicate functions/re-implemented helpers (pylint `duplicate-code` + the codebase-memory graph — `search_graph`/`query_graph` find same-named or same-shaped utilities across modules).
  - *Dead code*: `vulture` + graph queries for unreachable/orphan functions.
  - *Complexity creep*: `radon`/`xenon` cyclomatic-complexity gates; trend tracked between milestones, not just absolute values.
  - *Pattern inconsistency*: divergent error-handling, naming, or layering styles between modules written in different sessions.
  - *Architectural drift*: module dependencies vs the declared C4/ADR boundaries (pydeps graph as evidence).
  - *Test health*: coverage erosion, skipped/trivial tests, assertions that test nothing.
  - Output: a sanity report in `docs/sanity/` + `tech-debt` labeled GitHub issues for each finding, so cleanup enters the Kanban backlog like any other work.
- *AI-usage compliance (§6)* — **Álvaro's direct responsibility, not an agent's** (ADR-0005). He reviews declaration completeness, authorship balance, data protection, thesis consistency, and attribution himself, drawing on the audit trail (`docs/ai-usage/`, `Co-Authored-By` trailers) and the `ai-declaration` skill. A dedicated `ai-compliance-auditor` agent was tried and removed — Álvaro owns this judgment personally.

## 6. CI/CD (GitHub Actions, free for public repos)

- **`ci.yml`** (push + PR): `uv sync` → `ruff check` + `ruff format --check` → `mypy` → unit tests with coverage threshold (80% on `src/`) → integration tests (cassette-replayed, no live LLM) as a separate job. Concurrency-cancel on stale runs.
- **`system-tests.yml`**: nightly schedule + `workflow_dispatch` — docker compose lab targets (Juice Shop/DVWA) + full E2E retest scenarios (§8).
- **`docs.yml`**: on push to `main` — regenerate pyreverse UML + build MkDocs site → deploy to **GitHub Pages**. Diagrams are produced at build time from the current code, so they are structurally incapable of being stale (§11).
- **`sanity.yml`**: weekly schedule + `workflow_dispatch` — runs the mechanical sanity metrics (pylint duplicate-code, vulture, radon/xenon, coverage trend, pydeps) and uploads the report; the `codebase-sanity` agent consumes it to file `tech-debt` issues. `xenon` also runs as a hard complexity gate in `ci.yml` so egregious regressions never merge.
- **`security.yml`**: `pip-audit` (dependencies), `bandit` or Semgrep CI (SAST), `gitleaks` (history scan), plus **CodeQL** default setup. A security TFG with a clean security pipeline is also thesis material.
- **`thesis.yml`** (paths filter `thesis/**`): build PDF with `xu-cheng/latex-action` (xelatex + Carlito font installed), upload PDF artifact — every push produces a downloadable memoria.
- **`dependabot.yml`**: weekly, `pip` + `github-actions` ecosystems.
- **Branch protection** on `main`: require CI checks green, no force-push. (Solo dev → can't require approvals on own PRs; the PR template self-review checklist covers it.)

## 7. Marketplace plugins (verified available in `claude-plugins-official`)

Already enabled: `code-review`, `context7`, `github`, `frontend-design`.

Enable now:
- **`pyright-lsp`** — Python type intelligence while editing.
- **`pydantic-ai`** — current Pydantic AI patterns for the agent layer.
- **`semgrep`** — real-time SAST as code is written (dogfooding security in a security project).
- **`security-guidance`** — pattern warnings + LLM diff review on Claude-generated code.
- **`commit-commands`** — commit/push/PR workflow commands.
- **`pr-review-toolkit`** — specialized PR review agents (tests, error handling, type design).

Enable later when relevant:
- **`playwright`** — browser automation, for executing retest steps against web targets (core feature, next phase).
- **`hookify`**, **`skill-creator`**, **`claude-md-management`** — dev-time helpers if we iterate on the environment itself.

## 8. Testing strategy (test pyramid)

Layout `tests/unit/`, `tests/integration/`, `tests/system/` with pytest markers (`integration`, `system`) and Makefile targets `test-unit` / `test-integration` / `test-system` / `test` (all). Set up the structure, markers, CI wiring and conventions now; the tests themselves grow with the code.

- **Unit** — per-module, fully isolated, no network/LLM/docker. LLM-dependent code tested with Pydantic AI's `TestModel`/`FunctionModel` (no API calls, deterministic). Coverage threshold (80% on `src/`) measured here. Runs on every push/PR.
- **Integration** — real component interactions with fakes only at the outermost edge: report-parsing pipeline against synthetic sample reports in `tests/data/`, agent + tool wiring, persistence layer. LLM calls replayed from recorded cassettes (VCR-style) so CI is free and deterministic; a manually-triggered workflow can re-record against the live API. Runs on every PR as a separate CI job.
- **System (E2E)** — the full flow on the dockerized vulnerable lab (§4 `retest-lab`): synthetic pentest report in → findings extracted → retest executed against Juice Shop/DVWA containers → verdict (fixed / still vulnerable) asserted against known ground truth. Heavy: runs via docker compose in a `workflow_dispatch` + nightly-scheduled CI job, not on every PR. These same scenarios double as the **evaluation experiment** for the Results chapter (subobjective 4: time/reliability/effort vs manual retesting).
- **Acceptance** — each FR in the SRS (§10) carries acceptance criteria; each gets a test (usually at integration or system level) tagged with the requirement ID, feeding the traceability matrix (requirement → issue → PR → test).

## 9. Agile process & SE standards — Kanban adapted to solo dev + AI

AI-assisted solo development moves too fast for fixed-length sprints: cycle time per feature is hours/days, not weeks. So the process is **Kanban (continuous flow)** on a **GitHub Projects Kanban board**, documented as the methodology chapter of the memoria (an explicitly justified adaptation of Agile to a single developer working with an AI assistant — itself good thesis material):

- **Board columns**: `Backlog` → `Ready` → `In Progress` → `Verify` (automated: CI green, tests, doc/compliance checks, Claude pre-runs the change with the `verify` skill) → **`Validate` (human: Álvaro personally exercises the change)** → `Done`. Cards are GitHub issues fed by the SRS (§10); labels `req:FR-xx`, `req:nonfunctional`, `thesis`, `infra`; MoSCoW priority field.
- **Human validation protocol** — nothing reaches `Done` without Álvaro running it himself. Every PR must include a mandatory **"How to validate"** section (enforced by the PR template): the exact commands to run, the expected output/behavior, and the SRS acceptance criteria as a tick-box checklist. Where a feature isn't directly runnable yet, the PR ships a small demo script (`scripts/demo/`) or `make demo-<feature>` target so there is always something concrete to execute and judge. Álvaro's ticked checklist on the PR is also the durable evidence of human review that regulation §6 authorship requires.
- **WIP limit = 1–2** in `In Progress`: the bottleneck is Álvaro's review/validation capacity, not code production — limiting WIP keeps the `Validate` column from silting up with unreviewed AI output.
- **No sprints, no meetings**: flow is continuous; Álvaro replenishes `Ready` from the backlog whenever he chooses. Milestones mark **feature-complete increments**, not time boxes; each closes with a GitHub Release (SemVer + CHANGELOG). The `progress-report` skill becomes an on-demand summary (board movement + commits since a given date) for whenever a status write-up is useful.
- **Walking skeleton first**: the first increment is a minimal but *working* end-to-end slice — tiny synthetic report in → one finding parsed → one trivial check executed against a lab target → verdict out. Every later card expands a working product, never a pile of disconnected parts.
- **Definition of Done** (gate for `Validate` → `Done`): code + tests (per §8 level) + docstrings + affected docs/diagrams updated + CI green + "How to validate" executed and accepted by Álvaro.
- **Conventional Commits** enforced (hook + pre-commit); **ADRs** for architecture decisions (also evidences human authorship).
- **Flow metrics** (cycle time, throughput from board history) recorded — they quantify the AI-assisted speedup for the evaluation/conclusions chapters.

## 10. Requirements engineering (runs right after setup is verified)

Decisions with the user: **formal SRS catalogue**, elicited **immediately after setup** in the same working flow.

- **Elicitation — structured interview with Álvaro.** I ask focused question rounds (AskUserQuestion + free discussion); he answers; nothing is invented by AI. Topics, mapped to the four subobjectives in `docs/tfg_description`:
  1. *Report ingestion*: which pentest report formats (PDF, markdown, DefectDojo/JSON…), what fields define a finding (description, impact, attack vector, reproduction steps).
  2. *AI interpretation*: which LLM(s), local vs API, how reproduction steps become executable actions, human-in-the-loop checkpoints.
  3. *Retest execution*: target types (web, network…), execution environment (lab containers), safety boundaries — scope allowlist, non-destructive checks only, authorization model (competency IS5: risk management).
  4. *Evaluation*: metrics vs manual retesting (time, reliability, operator effort), what experiment will be run for the Results chapter.
  5. *Cross-cutting NFRs*: performance, auditability/reporting, ethics & legal constraints (competency IS6).
- **Specification — `docs/requirements/srs.md`**: ISO/IEC/IEEE 29148-style catalogue. FR-xx / NFR-xx with MoSCoW priority, acceptance criteria, source (interview date), and use cases for the main flows. This becomes the Requirements chapter of the memoria.
- **Traceability**: each FR → a GitHub issue labeled `req:FR-xx`; PRs reference issues; a traceability matrix (requirement → issue → PR → test) generated for the memoria appendix.
- **Validation**: Álvaro reviews and approves the SRS draft; scope decisions recorded as ADRs.
- A **`requirements` skill** (added to §4) scaffolds new SRS entries, keeps IDs consistent, and syncs the GitHub issues.

## 11. Documentation system (docs-as-code, auto-synced UML)

Principle: everything derivable from code is **generated, never hand-maintained** — so it cannot drift. (User asked about decorators for live-syncing docs; in Python the idiomatic mechanism is static analysis of docstrings/type hints, which achieves the same guarantee with zero runtime cost or boilerplate.)

- **API reference**: MkDocs + **Material** theme + **mkdocstrings\[python]** — pages generated from Google-style docstrings and type hints at build time.
- **UML class & package diagrams**: **pyreverse** (ships with pylint) regenerates them from the actual code on every docs build (`make docs`, CI, and Pages deploy). Any code change is reflected automatically.
- **Module dependency graph**: pydeps, same treatment.
- **Authored diagrams** (cannot be derived from code): architecture as **C4 model** (context/container/component) plus sequence and use-case diagrams, written in **Mermaid** inside markdown — rendered natively by GitHub and MkDocs Material, diffable in PRs. The `doc-curator` agent (§5) checks these for drift on every PR.
- **Publishing**: `docs.yml` deploys the site to GitHub Pages on every push to `main` — the tribunal gets a permanently up-to-date documentation URL to cite in the memoria.
- `docs/` markdown sources double as raw material for thesis chapters (design chapter ⇐ C4 + UML; implementation chapter ⇐ API docs).

## 12. Context & resource efficiency (big-project strategy)

This will grow large; the environment is designed so Claude works from **indexes and summaries, not raw file dumps**:

- **codebase-memory MCP** (already installed, enforced by the existing discovery-gate hook): keep the repo indexed; code exploration goes through `search_graph`/`trace_path`/`get_code_snippet` instead of reading whole files. ADRs can also be registered in the graph (`manage_adr`). This is also the **prevention** half of the duplication problem the `codebase-sanity` agent detects: CLAUDE.md mandates a graph search for an existing implementation before writing any new helper.
- **Lean, layered CLAUDE.md**: root file stays short (rules + pointers); per-directory `CLAUDE.md` only where a module needs local conventions. Skills carry the heavyweight knowledge and load **on demand** (that's their purpose — thesis style guide, lab setup, etc. cost zero context until invoked).
- **Durable memory in files, not conversation**: SRS, ADRs, and the AI-usage log are the project's long-term memory; any session can cold-start from them.
- **Subagents for bulk work**: wide exploration/research runs in Explore agents so the main session's context stays for decisions; one task per session, `/clear` between tasks.
- **Session discipline documented in CLAUDE.md**: start from the issue being worked, not from "read the project"; reference SRS/ADR sections by ID.

## Implementation order

1. `git init`, create public GitHub repo (`gh repo create`), initial scaffold (dirs, `.gitignore`, LICENSE, README).
2. `uv init` + `pyproject.toml` with ruff/mypy/pytest config; `Makefile`; `.pre-commit-config.yaml`.
3. Copy English template `docs/Plantillas TFG/PLANTILLA TFG_ENG/` → `thesis/`, apply Carlito font fix, verify local build (install TeX Live XeLaTeX + latexmk + ttf-carlito if missing).
4. Write `CLAUDE.md`.
5. Hooks scripts + project `settings.json` wiring.
6. Skills (`ai-declaration`, `thesis`, `adr`, `progress-report`, `requirements`, `docs`, `retest-lab` stub) + agents (`security-auditor`, `thesis-reviewer`, `doc-curator`, `codebase-sanity`).
6b. Documentation system: MkDocs Material + mkdocstrings + pyreverse/pydeps generation (`make docs`), C4/Mermaid seed pages, GitHub Pages deploy workflow.
7. GitHub workflows + dependabot + issue/PR templates; push; configure branch protection; create the GitHub Projects **Kanban board** (columns + WIP limits + MoSCoW priority field, §9).
8. Enable the marketplace plugins listed in §7.
9. Seed `docs/ai-usage/AI_USAGE_LOG.md` with the design/setup sessions held so far as the first entries.
10. Run the **requirements elicitation interview** (§10) and produce the SRS draft + traced GitHub issues for Álvaro's review.

## Verification

- **Hooks**: create `data/private/test.txt`, attempt Read → must be denied. Edit a `.py` file → ruff format runs. Attempt `git commit` with a bad message / a fake secret staged → blocked.
- **Toolchain**: `make lint typecheck test` all pass on the skeleton (one placeholder test per level so unit/integration/system wiring and markers are proven); `pre-commit run --all-files` clean.
- **Thesis**: `make thesis` produces `thesis/TFG.pdf` locally; `thesis.yml` uploads the PDF artifact in Actions.
- **CI**: first push shows `ci.yml` + `security.yml` green in GitHub Actions; CodeQL enabled.
- **AI tracking**: end a session → entry appears in `docs/ai-usage/sessions/`; run `/ai-declaration` → generates a draft declaration listing Claude Code with type of use and affected paths; run the `ai-compliance-auditor` agent → produces a compliance report flagging the seeded-by-hand early sessions as expected.
- **Docs**: `make docs` builds the site locally with generated UML class/package diagrams from the skeleton package; after first push to `main`, the GitHub Pages site is live.
