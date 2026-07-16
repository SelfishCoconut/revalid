import { useEffect, useRef, useState, type ReactNode } from "react";

import { useMutation } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { approveCommand, endRetestSession, rejectCommand, type SessionEvent } from "../api/client";
import { RetestTerminal } from "../components/RetestTerminal";
import { StatusBadge } from "../components/StatusBadge";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel } from "../components/ui/Panel";
import { useRetestSession } from "../hooks/useRetestSession";
import { errorMessage } from "../lib/format";
import { STATUS_META, type KnownStatus } from "../lib/status";

/**
 * Terminal lines are built from *executed* commands only — each `command_output`
 * event echoed as `$ <command>` followed by its stdout/stderr. A command that
 * was proposed but rejected (or is still awaiting approval) never ran, so it
 * never appears here: the docked terminal is a faithful log of the sandbox
 * shell, while the reasoning + gate live in the chat above it.
 */
function toTerminalLines(events: SessionEvent[]): string[] {
  const lines: string[] = [];
  for (const event of events) {
    if (event.kind !== "command_output") continue;
    lines.push(`$ ${String(event.payload.command ?? "")}`);
    const stdout = String(event.payload.stdout ?? "");
    const stderr = String(event.payload.stderr ?? "");
    if (stdout) lines.push(stdout);
    if (stderr) lines.push(stderr);
  }
  return lines;
}

/** A humanized, non-badge label for the session's own lifecycle status. */
function humanizeStatus(status: string): string {
  return status.replace(/_/g, " ");
}

/**
 * The verdict only ever carries a `VerdictStatus` in practice (the backend's
 * terminal determination), but the WS hook types it as a plain `string` so the
 * view doesn't trust the wire payload blindly. This guard narrows it to
 * `KnownStatus` so `StatusBadge` gets a real status with no cast — falling back
 * to no badge (rationale text still renders) if `lib/status.ts` doesn't
 * recognise the value.
 */
function isKnownStatus(status: string): status is KnownStatus {
  return status in STATUS_META;
}

/** One turn in the agent's voice: an iris-marked bubble in the center chat. */
function AgentTurn({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <span
        className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-iris shadow-[0_0_8px_var(--color-iris)]"
        aria-hidden
      />
      <div className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2/50 px-4 py-3">
        {children}
      </div>
    </div>
  );
}

/**
 * The agentic retest console (FR-17, Slice 1): a chat with the model in the
 * center — the agent's rationale, each gated command as a card with inline
 * approve/reject, and the verdict, as one scrolling conversation — over a
 * docked, collapsible terminal that shows only executed-command output. The
 * `/api` + WebSocket contract is unchanged from Slice 0; this is presentation.
 */
export function RetestSession() {
  const id = Number(useParams().id);
  const { events, status, verdict } = useRetestSession(id);
  const [terminalOpen, setTerminalOpen] = useState(true);
  const chatRef = useRef<HTMLDivElement>(null);

  // Each gate action is its own mutation so pending/error state stays scoped to
  // the button that triggered it — approving never disables Reject, and a failed
  // rejection doesn't blank out an unrelated approve error. Success still relies
  // on the WS stream (useRetestSession) to advance `status`; these mutations only
  // ever report their own request's pending/error state.
  const approveMutation = useMutation({
    mutationFn: (toolCallId: string) => approveCommand(id, toolCallId),
  });
  const rejectMutation = useMutation({
    mutationFn: (toolCallId: string) => rejectCommand(id, toolCallId),
  });
  const endMutation = useMutation({
    mutationFn: () => endRetestSession(id),
  });

  const terminalLines = toTerminalLines(events);
  const latestProposed = [...events].reverse().find((event) => event.kind === "command_proposed");
  const awaitingApproval = status === "awaiting_command" && latestProposed !== undefined;

  // Keep the newest turn in view as the transcript streams in. `scrollTop`
  // assignment is a no-op under jsdom, so tests need no scroll polyfill.
  useEffect(() => {
    const el = chatRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  const chatItems = events.flatMap((event) => {
    if (event.kind === "agent_message") {
      return [
        <AgentTurn key={event.seq}>
          <p className="whitespace-pre-wrap text-sm text-fg">{String(event.payload.text ?? "")}</p>
        </AgentTurn>,
      ];
    }
    if (event.kind === "command_proposed") {
      const isPending = awaitingApproval && event.seq === latestProposed?.seq;
      const toolCallId = String(event.payload.tool_call_id);
      return [
        <AgentTurn key={event.seq}>
          <p className="text-sm text-dim">{String(event.payload.rationale ?? "")}</p>
          <code className="mt-2 block overflow-x-auto rounded-md border border-line bg-ink/50 px-3 py-2 font-mono text-[13px] text-fg">
            <span className="text-faint">$</span> {String(event.payload.command ?? "")}
          </code>
          {isPending && (
            <div className="mt-3 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="positive"
                  disabled={approveMutation.isPending}
                  onClick={() => {
                    approveMutation.mutate(toolCallId);
                  }}
                >
                  Approve
                </Button>
                <Button
                  variant="danger"
                  disabled={rejectMutation.isPending}
                  onClick={() => {
                    rejectMutation.mutate(toolCallId);
                  }}
                >
                  Reject
                </Button>
                <span className="text-[11px] text-faint">runs once in the egress-locked sandbox</span>
              </div>
              {approveMutation.isError && (
                <p role="alert" className="text-sm text-danger-fg">
                  {errorMessage(approveMutation.error)}
                </p>
              )}
              {rejectMutation.isError && (
                <p role="alert" className="text-sm text-danger-fg">
                  {errorMessage(rejectMutation.error)}
                </p>
              )}
            </div>
          )}
        </AgentTurn>,
      ];
    }
    if (event.kind === "command_rejected") {
      const reason = String(event.payload.reason ?? "");
      return [
        <p key={event.seq} className="pl-5 text-[12px] text-faint">
          ✗ command rejected{reason ? `: ${reason}` : ""}
        </p>,
      ];
    }
    return [];
  });

  return (
    <div className="flex h-[calc(100dvh-9rem)] min-h-[28rem] flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <Eyebrow>Agentic retest session</Eyebrow>
          <span className="font-mono text-[12px] text-dim">{humanizeStatus(status)}</span>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Button
            variant="ghost"
            disabled={endMutation.isPending}
            onClick={() => {
              endMutation.mutate();
            }}
          >
            End session
          </Button>
          {endMutation.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(endMutation.error)}
            </p>
          )}
        </div>
      </div>

      {/* Chat — the center column, the agent's voice. */}
      <div
        ref={chatRef}
        role="log"
        aria-label="Agent conversation"
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex max-w-[52rem] flex-col gap-3 pb-1">
          {chatItems.length === 0 && !verdict && (
            <p className="text-sm text-dim">The agent is preparing its first step…</p>
          )}
          {chatItems}
          {verdict && (
            <div className="rounded-lg border border-line bg-panel-2/50 p-4">
              <div className="mb-2 flex items-center gap-2">
                <Eyebrow>Verdict</Eyebrow>
                {isKnownStatus(verdict.status) && <StatusBadge status={verdict.status} />}
              </div>
              <p className="text-sm text-fg">{verdict.rationale}</p>
            </div>
          )}
        </div>
      </div>

      {/* Terminal — docked at the bottom, collapsible, executed output only. */}
      <Panel className="shrink-0">
        <button
          type="button"
          onClick={() => {
            setTerminalOpen((open) => !open);
          }}
          aria-expanded={terminalOpen}
          className={`flex w-full items-center justify-between px-4 py-2.5 text-left ${
            terminalOpen ? "border-b border-line" : ""
          }`}
        >
          <Eyebrow>Terminal</Eyebrow>
          <span className="font-mono text-[11px] text-faint">
            {terminalLines.length} {terminalLines.length === 1 ? "line" : "lines"}{" "}
            {terminalOpen ? "▾" : "▸"}
          </span>
        </button>
        {terminalOpen && (
          <div className="p-3">
            <RetestTerminal lines={terminalLines} />
          </div>
        )}
      </Panel>
    </div>
  );
}
