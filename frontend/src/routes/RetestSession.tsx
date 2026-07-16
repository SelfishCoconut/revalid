import { useParams } from "react-router-dom";

import { approveCommand, endRetestSession, rejectCommand, type SessionEvent } from "../api/client";
import { RetestTerminal } from "../components/RetestTerminal";
import { StatusBadge } from "../components/StatusBadge";
import { Button } from "../components/ui/Button";
import { Panel, PanelHeader } from "../components/ui/Panel";
import { useRetestSession } from "../hooks/useRetestSession";
import { STATUS_META, type KnownStatus } from "../lib/status";

/** Terminal transcript lines derived from the session's ordered event log. */
function toLines(events: SessionEvent[]): string[] {
  const lines: string[] = [];
  for (const event of events) {
    if (event.kind === "command_proposed") {
      lines.push(`$ ${String(event.payload.command)}`);
    }
    if (event.kind === "command_output") {
      const stdout = String(event.payload.stdout ?? "");
      const stderr = String(event.payload.stderr ?? "");
      if (stdout) lines.push(stdout);
      if (stderr) lines.push(stderr);
    }
  }
  return lines;
}

/** A humanized, non-badge label for the session's own lifecycle status. */
function humanizeStatus(status: string): string {
  return status.replace(/_/g, " ");
}

/**
 * The verdict banner only ever carries a `VerdictStatus` in practice (the
 * backend's terminal determination), but the WS hook types it as a plain
 * `string` so the view doesn't have to trust the wire payload blindly. This
 * guard narrows it to `KnownStatus` so `StatusBadge` gets a real status with
 * no cast — falling back to no badge (rationale text still renders) if the
 * value is ever something `lib/status.ts` doesn't recognise.
 */
function isKnownStatus(status: string): status is KnownStatus {
  return status in STATUS_META;
}

/** The agentic retest session view: live terminal, command gate, verdict (FR-17). */
export function RetestSession() {
  const id = Number(useParams().id);
  const { events, status, verdict } = useRetestSession(id);

  const lines = toLines(events);
  const latestProposed = [...events].reverse().find((event) => event.kind === "command_proposed");
  const awaitingApproval = status === "awaiting_command" && latestProposed !== undefined;

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader
          eyebrow="Agentic retest session"
          aside={<span className="font-mono text-[12px] text-dim">{humanizeStatus(status)}</span>}
        />
        <div className="p-4">
          <RetestTerminal lines={lines} />
        </div>
      </Panel>

      {awaitingApproval && latestProposed && (
        <Panel>
          <PanelHeader eyebrow="Awaiting approval" />
          <div className="space-y-2 p-4">
            <p className="font-mono text-sm text-fg">{String(latestProposed.payload.command)}</p>
            <p className="text-sm text-dim">{String(latestProposed.payload.rationale)}</p>
            <div className="flex gap-2 pt-2">
              <Button
                variant="positive"
                onClick={() => {
                  void approveCommand(id, String(latestProposed.payload.tool_call_id));
                }}
              >
                Approve
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  void rejectCommand(id, String(latestProposed.payload.tool_call_id));
                }}
              >
                Reject
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {verdict && (
        <Panel>
          <PanelHeader
            eyebrow="Verdict"
            aside={isKnownStatus(verdict.status) ? <StatusBadge status={verdict.status} /> : null}
          />
          <p className="p-4 text-sm text-fg">{verdict.rationale}</p>
        </Panel>
      )}

      <Button
        variant="ghost"
        onClick={() => {
          void endRetestSession(id);
        }}
      >
        End session
      </Button>
    </div>
  );
}
