# 0026. Operator manual commands (`!`) via discrete exec — not a shared PTY

Date: 2026-07-16
Status: accepted
## Context

FR-17 Slice 2 (epic [#87](https://github.com/SelfishCoconut/revalid/issues/87),
issue [#92](https://github.com/SelfishCoconut/revalid/issues/92)) gives the
operator a way to **take over the agent's shell**: run a manual command in the
live retest session and have the agent **observe it on its next turn**. The
design spec's original vision was *one shared interactive terminal* (xterm ↔
WebSocket ↔ container PTY) that both the agent and the human type into.

We researched how comparable tools handle this before committing:

- **Pure AI shell agents** (Claude Code, Aider, OpenAI's shell tool, Codex CLI)
  run **each command as a discrete subprocess** with captured stdout/stderr/exit
  code, non-interactive. They deliberately avoid a persistent interactive shell
  (clean per-command results, context-window control, safety); Claude Code even
  keeps cwd between calls but not env/functions.
- **Web terminals** (Wetty, ttyd, VS Code, Cloud Shell, Gitpod) all bridge
  **xterm.js ↔ WebSocket ↔ a backend PTY** — a solved, standard pattern, but
  human-only: nothing extracts per-command results for a program to reason on.
- **Terminals that do both** a real PTY *and* programmatic per-command results
  (VS Code command detection, iTerm2, WezTerm, Warp, Windows Terminal) use
  **OSC 133 shell integration**: the shell emits standardized escape-sequence
  markers (`133;A/B/C/D;<exitcode>`) around every command, parsed out of the byte
  stream. This is the industry-standard, robust version of an end-marker sentinel.

Álvaro's decision after reviewing this: **do not build a shared PTY.** Do what
Claude Code does — discrete execs — and add the one affordance he wants: a
Claude-Code-style **`!` command** the human types to run a one-shot command in
the session's sandbox, which the agent then observes.

Forces:

- **Usefulness vs. cost.** A true shared PTY (OSC 133 + a bash+tools image + a
  bidirectional streaming transport replacing the DB-poll) is a large, higher-
  risk build. For *this* tool the human reads their own command output with their
  eyes; the agent only needs a clean result for its own commands and an
  *observation* of the human's — neither requires a shared TTY.
- **Reuse.** Slice 0's discrete `sandbox.exec` + append-only transcript + DB-poll
  WS already deliver everything a `!` command needs. No new transport, no image
  change, no OSC-133 parsing.
- **Human-in-the-loop invariants (ADR-0025) unchanged.** The agent's commands
  stay gated (propose → approve). The human's own commands are **ungated** —
  single trusted user, app on `127.0.0.1` (ADR-0008) — and contained by the
  egress-locked sandbox.

## Decision

Add an **operator manual-command path**, discrete-exec, no shared PTY:

- **`POST /api/retest-sessions/{id}/human-command` `{command}`** runs the command
  **ungated** in the live session's sandbox through the *same* `sandbox.exec` the
  agent's `run_command` tool uses (one-shot; clean stdout/stderr/exit code). A
  no-op once the session is no longer live (the sandbox is gone).
- The command + result are recorded as a **`HUMAN_COMMAND`** transcript event (so
  the docked terminal shows it, marked `operator$` apart from the agent's `$`).
- The agent **observes** it on its next turn: the command is buffered on the live
  session and **surfaced through the next tool-result context** — appended to the
  `run_command` result on approve, or folded into the tool **denial** on reject.
  This deliberately avoids injecting a user message mid-deferred-run (which would
  fight Pydantic AI's tool-call/tool-return pairing); the observation is drained
  atomically (lock-guarded) so it is delivered exactly once.
- Commands are **stateless between calls** (no persistent shell, so no cwd/env
  carry-over) — matching Claude Code's model; adequate for HTTP-shaped retests.
- Frontend: a single console line under the docked terminal — **`!<command>`**
  runs it; plain text is reserved for chat-to-agent steering (a later slice) and
  only hinted.

This **supersedes the shared-PTY sketch** in the design spec §2 and the earlier
Slice 2 plan. The reproducibility framing (ADR-0025) is unchanged: the verdict is
a human-adjudicated judgment; the replayable transcript — now including
`HUMAN_COMMAND` events — is the audit trail.

## Consequences

- **Good:** small, fully unit/integration-testable (FakeSandbox + a scripted
  `FunctionModel` proving the agent reads the operator activity on both approve
  and reject) — 367 backend tests @ 99% coverage; no new dependency, transport,
  or image. Ships the useful capability now.
- **Accepted limitations (stated plainly for the thesis):** no live byte-level
  streaming (output appears when the command returns, bounded by the per-command
  timeout — no interactive/full-screen programs); no shared shell state between
  commands; the human sees the agent's commands as captured blocks, not a live
  TTY. A true shared PTY (OSC 133 shell integration + a bash+tools image) remains
  a possible future upgrade if a real interactive session is ever needed.
- **Invariants preserved:** agent commands stay gated (ADR-0025); egress lock
  (NFR-03) and the single-user threat model (ADR-0008) are untouched; the old
  batch path still coexists until the last slice.

## References

- Design spec: `docs/superpowers/specs/2026-07-16-agentic-retest-console-design.md` (§2 shared-terminal vision, now superseded for the retest console)
- Plan: `docs/superpowers/plans/2026-07-16-agentic-retest-console-slice-2.md`
- Builds on ADR-0025 (agentic retest console); epic [#87](https://github.com/SelfishCoconut/revalid/issues/87), issue [#92](https://github.com/SelfishCoconut/revalid/issues/92)
- Shell-integration prior art: VS Code Terminal Shell Integration (OSC 133/633); OSC 133 (FinalTerm FTCS); Claude Code / OpenAI shell tool (discrete capture)
