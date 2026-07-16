# Agentic Retest Console — Slice 2 Implementation Plan (operator manual commands)

- **Date**: 2026-07-16
- **Issue**: [#92](https://github.com/SelfishCoconut/revalid/issues/92) · Epic [#87](https://github.com/SelfishCoconut/revalid/issues/87)
- **Records**: **ADR-0026** — operator manual commands (`!`) via discrete exec, not a shared PTY
- **Builds on**: Slice 0 (sandbox, agent, orchestrator, transcript, REST/WS) and Slice 1 (chat-centric console)

## Scope (final — see ADR-0026)

Álvaro's decision after researching how comparable tools handle the shell (Claude
Code / Aider / OpenAI shell tool use **discrete execs**; VS Code / iTerm2 / Warp
use **OSC 133** to reconcile a real PTY with per-command results): **no shared
PTY.** Do what Claude Code does — discrete execs — plus a Claude-Code-style **`!`
command** the operator types to run a one-shot command in the session's sandbox,
which the agent then **observes on its next turn**.

The earlier draft of this plan (a shared PTY + OSC 133 + streaming transport +
bash image) is **superseded**; that stays a possible future upgrade.

## What shipped

**Backend** (`feat(retest)` commit):
- `domain.SessionEventKind.HUMAN_COMMAND` — a distinct transcript event for
  operator-run commands.
- `retest_session.submit_human_command(session, registry, id, command)` — runs
  the command **ungated** (ADR-0008) through the same `sandbox.exec` the agent
  uses; records a `HUMAN_COMMAND` event; buffers the command on the live session.
  A no-op once the session is no longer live.
- **Agent observation**: buffered operator activity is surfaced through the next
  tool-result context — appended to the `run_command` result on approve
  (`retest_agent.format_observations` + `RetestSessionDeps.drain_observations`),
  or folded into the tool **denial** on reject. Drained atomically
  (`LiveSession.observe`/`drain`, lock-guarded) so it is delivered exactly once.
  (Avoids mid-deferred-run message-history surgery — the one framework risk.)
- `app`: `POST /api/retest-sessions/{id}/human-command` `{command}` (202,
  background worker `run_human_command`), empty command → 422.
- Tests: records + buffers; agent-observes-on-approve and on-reject (scripted
  `FunctionModel` → verdict rationale `saw-operator`); dead-session no-op;
  endpoint + empty-command 422. **367 unit+integration @ 99% coverage.**

**Frontend** (`feat(ui)` commit):
- `api/client.submitHumanCommand(id, command)`.
- `RetestSession`: a single console line under the docked terminal — `!<command>`
  runs it in the sandbox; plain text is reserved for chat-to-agent steering (a
  later slice) and only hinted. `human_command` events render in the terminal
  marked `operator$` (vs the agent's `$`). Input disabled once the session is
  over.
- Tests: runs a `!command`; non-`!` hint path; operator commands in the terminal;
  disabled when over. **104 vitest, all frontend gates green.**

## Validate

- `make demo-retest-session` (FakeSandbox + FunctionModel) — still green; extended
  to show a `!` command being observed.
- `make run` → start a retest session, type `!curl -s http://revalid-juice-shop:3000/rest/products`
  in the console line, watch it appear in the terminal, then let the agent resume
  and reference it.
- Backend: `make test-unit` / `test-integration`; frontend: `npm run lint && tsc && build && test:coverage`.

## Docs updated with the PR

ADR-0026 (+ index), this plan, design spec §2/§3 (Slice 2 wording), roadmap M6,
epic #87, issue #92 title/body.
