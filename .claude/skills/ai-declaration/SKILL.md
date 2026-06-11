---
name: ai-declaration
description: Generate or update the mandatory AI-usage declaration for the thesis (Reglamento TFG 2026 §6) from the audit trail in docs/ai-usage/ and git history. Use when the user asks to write, update, or check the AI declaration, or before a thesis deposit.
---

# AI-usage declaration generator

The ESII TFG regulation (2026, §6) requires the thesis to declare: **(1) which AI tools were used, (2) the type of use, (3) the affected sections**. This skill compiles that declaration from evidence, never from memory.

## Sources (in order)

1. `docs/ai-usage/AI_USAGE_LOG.md` — curated one-entry-per-session log (authoritative for *type of use*).
2. `docs/ai-usage/sessions/*.md` — raw auto-logged session records (cross-check completeness).
3. Git history: `git log --grep="Co-Authored-By: Claude" --stat` — AI-assisted commits and the paths they touched (authoritative for *affected areas*).

## Procedure

1. Aggregate the sources. If a period with Claude-trailer commits has no curated log entry, STOP and ask Álvaro to describe that session's type of use before generating — never invent.
2. Classify usage into categories: code generation, code review/refactoring, test writing, documentation generation, thesis writing assistance (language/editing), research/ideation. Map affected repo paths → thesis sections/chapters where applicable.
3. Write/update the declaration in the thesis methodology chapter (`thesis/chapters/`, section "Use of Artificial Intelligence Tools"). Structure:
   - Tools used (Claude Code + model family, versions if known; any other AI tool Álvaro reports).
   - Type of use, per category with concrete scope ("unit-test scaffolding for the report parser", not "helped with code").
   - Affected sections: thesis chapters/sections plus repo areas, with a pointer to the public audit trail (`docs/ai-usage/` in the repo).
   - Authorship statement: all design decisions made by the author; all AI output reviewed and validated by the author (PR validation checklists + ADRs as evidence).
4. Keep it factual and sober — no marketing language. Spanish regulation, English thesis: write the declaration in English.

## Hard rules

- Never minimize or omit usage. Under-declaration is an academic-integrity risk for Álvaro.
- If usage can't be evidenced, say so explicitly in the declaration draft and flag it.
