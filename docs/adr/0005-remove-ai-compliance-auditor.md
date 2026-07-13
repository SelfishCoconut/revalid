# 0005. Remove the ai-compliance-auditor agent; Álvaro owns §6 compliance directly

Date: 2026-07-13
Status: accepted

## Context

ADR-0001 set up an `ai-compliance-auditor` agent to audit Álvaro's AI-usage
practices against the TFG regulation (§6) — declaration completeness, authorship
balance, data protection, thesis consistency, and attribution — and mandated
running it before each milestone release.

In practice, §6 compliance is a judgement the author must make and defend
personally: the regulation makes Álvaro responsible for reviewing all AI output
and for the truthfulness of the AI-usage declaration. Delegating that judgement
to an agent risks the exact failure mode §6 guards against — treating a
compliance verdict as something the AI produces rather than something the author
owns. The audit trail the agent would read (`docs/ai-usage/`, `Co-Authored-By`
trailers, ADRs) already exists and is maintained independently.

## Decision

We will **remove the `ai-compliance-auditor` agent entirely**. §6 AI-usage
compliance is **Álvaro's direct responsibility**: he reviews declaration
completeness, authorship evidence, data protection, thesis consistency, and
attribution himself, using the existing audit trail and the `ai-declaration`
skill. No agent is run for compliance.

- Delete `.claude/agents/ai-compliance-auditor.md` and every instruction to run
  it (`CLAUDE.md`, `docs/roadmap.md`, `docs/development-plan.md`, `doc-curator`).
- The pre-release gate keeps only the `codebase-sanity` agent (code quality).
- Unchanged: the §6 obligations themselves — the audit trail, the
  `Co-Authored-By: Claude` trailer, the `ai-declaration` skill, the quarantined
  `data/private/`, and the data-protection rules all stand.

This supersedes the `ai-compliance-auditor` element of ADR-0001's AI-governance
decision; the rest of ADR-0001 stands.

## Alternatives considered

- **Keep the agent, run it before releases (ADR-0001).** Rejected by Álvaro:
  a compliance verdict is the author's to make; an agent producing it blurs the
  authorship line §6 exists to protect.
- **Keep the agent but make it advisory/on-demand only.** Rejected: leaves a
  standing tool whose output could be mistaken for the compliance sign-off, for
  no benefit over the author reading the same audit trail directly.

## Consequences

- **Clearer authorship:** the §6 compliance judgement is unambiguously Álvaro's,
  which is itself the strongest evidence of human authorship.
- **One less agent** to maintain; the pre-release ritual is just `codebase-sanity`.
- **Accepted:** no automated pre-flight for declaration/attribution gaps — Álvaro
  performs that review manually (the audit trail makes it a quick pass).
