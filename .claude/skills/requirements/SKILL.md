---
name: requirements
description: Create, update, or audit requirements in the SRS (docs/requirements/srs.md) and keep them in sync with labeled GitHub issues. Use for "add a requirement", "update FR-xx", "sync requirements to issues", or traceability checks.
---

# Requirements management (SRS ↔ GitHub issues)

The SRS at `docs/requirements/srs.md` is the single source for requirements (ISO/IEC/IEEE 29148-style catalogue). Every functional requirement maps 1:1 to a GitHub issue on the Kanban board.

## Requirement format

```markdown
### FR-12 — <imperative title>
- **Priority**: Must | Should | Could | Won't (MoSCoW)
- **Source**: elicitation interview YYYY-MM-DD | ADR-NNNN | change request
- **Description**: The system shall …  (one testable behavior per requirement)
- **Acceptance criteria**:
  - [ ] Concrete, executable check 1
  - [ ] Concrete, executable check 2
- **Traces to**: issue #N, tests `tests/…`
```

NFRs use `NFR-xx` with a measurable target ("p95 parse time < 30 s per report", never "fast").

## Rules

- IDs are immutable and never reused. New requirement = next free number.
- A requirement must be testable; if you can't phrase acceptance criteria as something runnable/checkable, split or rephrase it.
- Requirements changes are Álvaro's decisions — draft, then confirm before committing. Scope changes also get an ADR.
- Sync: each FR gets a GitHub issue titled `FR-xx: <title>` with labels `req:FR-xx` + MoSCoW priority field set on the board (`gh issue create` / `gh project item-add`). When an FR changes, update its issue body.
- Traceability audit (on request / before milestones): every FR has an issue; every closed FR-issue has merged PRs referencing it and tests tagged with the ID; report orphans.
