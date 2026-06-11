# 0001. Development environment, AI governance and Kanban process

Date: 2026-06-11
Status: accepted

## Context

The TFG "AI-Driven System for the Revalidation of Pentest Findings" is developed by a single
developer (Álvaro Navarro) assisted by Claude Code, under the ESII TFG regulation (Feb 2026),
whose §6 imposes: mandatory declaration of AI usage (tools, type of use, affected sections),
full author responsibility for authorship, and a prohibition on feeding personal/protected data
to AI tools. The project needs an environment that makes compliance and code quality structural
rather than aspirational, before any product code is written.

## Decision

Álvaro decided to adopt the environment specified in `docs/development-plan.md`:

- **Stack**: Python 3.12+ / uv; Pydantic AI for the agent layer; ruff + mypy strict + pytest pyramid (unit/integration/system, 80% coverage gate).
- **Monorepo**: code, thesis (ESII XeLaTeX template, Carlito font substitution) and docs in one public GitHub repo (`revalid`) with full CI/CD.
- **AI governance**: automated session logging + `Co-Authored-By` commit trailers + `ai-declaration` skill generating the thesis declaration from evidence; `ai-compliance-auditor` agent auditing practices; quarantined `data/private/` enforced by a PreToolUse hook.
- **Process**: Kanban (GitHub Projects) with WIP limit 1–2 and a two-stage gate: automated `Verify`, then human `Validate` — every PR ships mandatory "How to validate" steps that Álvaro executes personally before merge. No sprints, no scheduled meetings.
- **Documentation**: docs-as-code — mkdocstrings API docs and pyreverse UML generated from code on every build; authored C4/sequence diagrams in Mermaid reviewed for drift by the `doc-curator` agent.
- **License**: Beerware (Rev. 42), Álvaro's explicit choice.

## Alternatives considered

- **Scrum with fixed sprints** — rejected: AI-assisted solo cycle time (hours/days) makes time-boxed sprints ceremony without benefit; flow-based Kanban matches reality.
- **Runtime decorators to sync diagrams with code** — rejected in favor of static analysis (pyreverse/mkdocstrings): same can't-be-stale guarantee, no runtime cost or boilerplate.
- **Two repos (code / thesis)** — rejected: splits the AI-usage audit trail and doubles CI setup.
- **Manual AI-usage logging only** — rejected: reconstructing "type of use per section" months later is error-prone; automation makes the audit trail dependable.

## Consequences

- Compliance evidence (logs, trailers, validation checklists, ADRs) accumulates as a by-product of normal work; the thesis declaration is generated, not remembered.
- Every merge costs Álvaro a hands-on validation pass — deliberate: it is both the quality gate and the authorship evidence.
- Public repo means discipline about synthetic data is absolute from day one.
- The environment itself (this ADR, the plan, the agents) becomes content for the thesis methodology chapter.
