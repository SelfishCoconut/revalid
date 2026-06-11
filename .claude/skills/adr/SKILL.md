---
name: adr
description: Create or update an Architecture Decision Record in docs/adr/ (MADR format). Use whenever a significant design/architecture/scope decision is made, or when the user says "record this decision".
---

# Architecture Decision Records

ADRs document the decisions Álvaro makes — they are also the evidence of human authorship the TFG regulation requires. A decision without an ADR doesn't exist.

## Format (MADR, one file per decision)

File: `docs/adr/NNNN-short-kebab-title.md` (NNNN = next sequential number, check existing files).

```markdown
# NNNN. <Title — the decision, stated as a fact>

Date: YYYY-MM-DD
Status: accepted | superseded by [NNNN](link) | deprecated

## Context
What forces are at play; why a decision is needed now.

## Decision
What Álvaro decided. Active voice: "We will…"

## Alternatives considered
Each rejected option and the concrete reason it lost.

## Consequences
What becomes easier, what becomes harder, what debt is accepted.
```

## Rules

- The decision-maker is Álvaro. If he hasn't explicitly decided, draft the ADR with status `proposed` and ask him — never mark `accepted` on his behalf.
- Update `docs/adr/README.md` index (number, title, status, date).
- Also register it in the codebase-memory graph with `manage_adr` so structural queries can surface it.
- Supersede, don't edit history: a changed decision gets a new ADR linking back.
