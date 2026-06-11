# AI usage log (curated)

Public audit trail of AI assistance in this TFG, as required by the ESII TFG regulation
(Feb 2026, §6). One entry per work session: date, tool, type of use, affected areas.
Raw auto-generated session records live in [`sessions/`](sessions/).

> Maintained by the author. The thesis declaration is generated from this log plus git
> history (`Co-Authored-By: Claude` trailers) by the `ai-declaration` skill.

| Date | Tool | Type of use | Affected areas |
|------|------|-------------|----------------|
| 2026-06-11 | Claude Code (Fable 5) | Research & analysis: read TFG regulations and proposal; summarized AI-usage compliance constraints. Design assistance: drafted the development-environment plan (stack, process, compliance tooling) iteratively refined and approved by the author. | `docs/development-plan.md`, ADR-0001 |
| 2026-06-11 | Claude Code (Fable 5) | Code/config generation under author direction: repo scaffold, toolchain config, CI workflows, hooks, skills, agents, docs site, thesis template adaptation (Carlito font). All reviewed by the author. | repo scaffold (`pyproject.toml`, `.claude/`, `.github/`, `Makefile`, `mkdocs.yml`, `thesis/TFG.tex`, `CLAUDE.md`) |
| 2026-06-11 | Claude Code (Fable 5) | Code generation under author direction: resolved+merged Dependabot PR #1; implemented FR-02 slice (domain model, SQLite layer, FastAPI factory, DefectDojo-style JSON ingestion, tests, demo) on PR #23, pending author validation. | `src/revalid/`, `tests/`, `scripts/demo/`, `docs/roadmap.md` |
