---
name: progress-report
description: Generate an on-demand progress summary (board movement, commits, closed issues, open questions) since a given date. Use when the user asks for a status write-up or progress report.
---

# Progress report

On-demand summary of project movement since a date Álvaro specifies (default: last report in `docs/reports/`, else last 14 days).

## Gather

- `git log --oneline --since=<date>` + diffstat for areas touched.
- `gh issue list --state closed --search "closed:><date>"` and currently `In Progress`/`Validate` cards on the Kanban board (`gh project item-list`).
- Milestone status: `gh api repos/:owner/:repo/milestones`.
- Flow metrics where available: cycle time of cards closed in the period (created→closed timestamps), throughput (cards/week).

## Output

Short markdown report saved to `docs/reports/YYYY-MM-DD.md`:

1. **Done** — features merged & validated (issue refs, one line each).
2. **In flight** — cards in progress/validate and what blocks them.
3. **Metrics** — throughput, cycle time, coverage trend.
4. **Decisions taken** — ADRs created in the period.
5. **Open questions / risks** — things needing Álvaro's decision.

Factual, terse, no filler. These reports feed the thesis "project development" narrative and quantify the AI-assisted pace.
