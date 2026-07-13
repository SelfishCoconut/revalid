# 0004. Right-size the solo-developer process: ceremony scales with thesis value

Date: 2026-07-13
Status: accepted

## Context

ADR-0001 set up a deliberately enterprise-flavoured process (Kanban card →
branch → PR with a full "How to validate" section → CI → review) and mandated
that all code discovery go through the codebase-memory graph, enforced by a
blocking `PreToolUse` hook that refuses `Read`/`Grep` until a graph query runs.

That process is graded methodology, not pure overhead: the TFG tribunal rewards
requirements engineering, traceability, and recorded design rationale, and §6
requires evidence that Álvaro reviews AI output. So the spine (SRS/FR-IDs, ADRs,
AI-usage logging, CI gates, generated docs) earns its keep.

But two parts impose team-shaped cost on a one-person project with a ~300-line
`src/`:

1. **Uniform PR ceremony.** A three-file settings fix went through the same
   card→branch→PR→"How to validate"→auto-merge cycle as a feature. The heavy
   template is valuable exactly when a PR maps to a thesis acceptance criterion,
   and is noise otherwise.
2. **The forced codebase-memory discovery gate.** A knowledge graph pays off at
   ten-thousands of LOC; over six source files it adds latency and blocks basic
   reads for a benefit that has not yet materialised at this scale. It blocked
   routine reads twice in one session.

The trigger was Álvaro pausing mid-chore to ask whether the workflow was too
complex for a solo TFG.

## Decision

We will **scale process ceremony to a change's thesis value**, keeping the
governance spine intact:

- **Kanban board stays.** It is Álvaro's way to see progress and share status
  with others; its value is independent of team size. Board automation is
  unchanged.
- **Full PR ceremony is reserved for FR/NFR-carrying PRs.** A PR that implements
  a requirement still fills the complete "How to validate" section (commands,
  expected output, acceptance-criteria checkboxes) — that is thesis evidence.
  Trivial chores (`chore:`/`ci:`/`docs:` with no requirement) may use a short PR
  body; branch protection and required CI are unchanged, so `main` is still
  never pushed directly and every change is still CI-gated.
- **The codebase-memory discovery-gate hook is disabled for now.** The blocking
  `PreToolUse` gate and its `SessionStart` reminder are removed from the local
  Claude config; the removed config is preserved at
  `~/.claude/hooks/cbm-hooks.disabled.json` for one-step reactivation. The
  **MCP server stays enabled** — `search_graph`/`trace_path`/`get_code_snippet`
  remain available for voluntary use. Álvaro will ask to re-enable the gate when
  the codebase is large enough to warrant it.
- Unchanged and still non-negotiable (§6): Álvaro owns all design decisions
  (recorded as ADRs), the `Co-Authored-By: Claude` trailer, data-protection
  rules, and the AI-usage declaration. ADR-0003's CI-gated auto-merge stands.

This amends the *ceremony* of ADR-0001 (uniform per-item PR weight, mandatory
graph-first discovery); ADR-0001's governance spine and ADR-0002/0003 stand.

## Alternatives considered

- **Keep everything as-is (ADR-0001 unchanged).** Rejected: the per-chore PR
  weight and the blocking discovery gate cost more than they return at current
  scale, and risk process outrunning the product — M1 was still unfinished while
  effort went to tooling.
- **Drop the Kanban board and its automation.** Rejected by Álvaro: the board is
  his progress-visibility and sharing surface, valuable regardless of team size.
- **Delete the codebase-memory tooling outright.** Rejected: the graph is
  expected to earn its keep as the codebase grows; disabling the *forced gate*
  while keeping the MCP available preserves that option at zero ongoing cost.
- **Lighten the auto-merge governance too.** Deferred: ADR-0003's async-review +
  revert model is also §6 review evidence; no reason to touch it now.

## Consequences

- **Easier:** small fixes land with proportionate overhead; routine reads are no
  longer blocked; attention refocuses on the FR/NFR slices that produce both
  running software and thesis chapters.
- **Preserved:** every requirement PR still carries full validation evidence;
  the SRS/ADR/AI-usage spine, CI gates, and the Kanban board are untouched.
- **Accepted debt:** graph-backed discovery is now opt-in, so structural queries
  happen only when a human or agent chooses them; re-enabling the gate is a
  documented one-step restore when scale justifies it.
