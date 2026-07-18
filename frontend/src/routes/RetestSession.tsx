import { useEffect, useRef, useState, type ReactNode } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import {
  adjudicateSession,
  approveCommand,
  endRetestSession,
  getRetestSession,
  rejectCommand,
  setFreeLaunch,
  submitHumanCommand,
  submitMessage,
  type SessionEvent,
} from "../api/client";
import { RetestTerminal } from "../components/RetestTerminal";
import { StatusBadge } from "../components/StatusBadge";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel } from "../components/ui/Panel";
import { useRetestSession } from "../hooks/useRetestSession";
import { errorMessage } from "../lib/format";
import {
  autoApprovedSeqs,
  budgetLabel,
  currentFreeLaunch,
  givenUpReason,
  stepsUsed,
} from "../lib/sessionBudget";
import { STATUS_META, type KnownStatus } from "../lib/status";
import type { VerdictStatus } from "../api/types";

/** The three verdicts a human can adjudicate to (FR-09 / FR-17 Slice 6a). */
const VERDICT_STATUSES: readonly VerdictStatus[] = ["still_open", "fixed", "inconclusive"];

/**
 * Terminal lines are built from *executed* commands only — each `command_output`
 * event echoed as `$ <command>` followed by its stdout/stderr. A command that
 * was proposed but rejected (or is still awaiting approval) never ran, so it
 * never appears here: the docked terminal is a faithful log of the sandbox
 * shell, while the reasoning + gate live in the chat above it.
 */
/** Append one command (with a prompt marker) and its stdout/stderr to the terminal. */
function pushCommand(lines: string[], prompt: string, payload: Record<string, unknown>): void {
  lines.push(`${prompt} ${String(payload.command ?? "")}`);
  const stdout = String(payload.stdout ?? "");
  const stderr = String(payload.stderr ?? "");
  if (stdout) lines.push(stdout);
  if (stderr) lines.push(stderr);
}

function toTerminalLines(events: SessionEvent[]): string[] {
  const lines: string[] = [];
  for (const event of events) {
    // Agent-run commands use a bare `$`; the operator's own `!` commands are
    // marked so the two voices are distinguishable in the same shared log.
    if (event.kind === "command_output") pushCommand(lines, "$", event.payload);
    else if (event.kind === "human_command") pushCommand(lines, "operator$", event.payload);
  }
  return lines;
}

/** Session statuses past which the sandbox is gone, so `!` commands can't run. */
const OVER_STATUSES = new Set(["concluded", "ended", "given_up", "error"]);

/** Extract the ordered plan steps from an event payload (defensively typed). */
function payloadSteps(payload: Record<string, unknown>): string[] {
  return Array.isArray(payload.steps) ? payload.steps.map(String) : [];
}

/** The current guiding plan = the steps of the latest approved `plan_updated` event. */
function currentPlan(events: SessionEvent[]): string[] {
  const latest = [...events].reverse().find((event) => event.kind === "plan_updated");
  return latest ? payloadSteps(latest.payload) : [];
}

/** A compact ordered list of guiding-plan steps. */
function StepList({ steps }: { steps: string[] }) {
  return (
    <ol className="mt-2 space-y-1">
      {steps.map((step, i) => (
        <li key={`${String(i)}-${step}`} className="flex gap-2 text-sm text-fg">
          <span className="font-mono text-[12px] text-iris-fg">{i + 1}.</span>
          <span>{step}</span>
        </li>
      ))}
    </ol>
  );
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

/** One operator chat message: a right-aligned, iris-tinted bubble in the center chat. */
function HumanTurn({ text, queued }: { text: string; queued: boolean }) {
  return (
    <div className="flex justify-end">
      <div className="min-w-0 max-w-[85%] rounded-lg border border-iris/40 bg-iris/10 px-4 py-3">
        <p className="whitespace-pre-wrap text-sm text-fg">{text}</p>
        {queued && (
          <p className="mt-1 text-[11px] text-faint">queued — sent on your next approve/reject</p>
        )}
      </div>
    </div>
  );
}

/** Seq of the latest approve/reject; a human_message after it hasn't been delivered yet. */
function lastDecisionSeq(events: SessionEvent[]): number {
  const decisions = new Set(["command_approved", "command_rejected"]);
  const latest = [...events].reverse().find((event) => decisions.has(event.kind));
  return latest ? latest.seq : 0;
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
  const [input, setInput] = useState("");
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
  // The operator's own commands (`!`) run ungated in the same sandbox; separate
  // mutation so its pending/error state is independent of the gate buttons.
  const humanCommandMutation = useMutation({
    mutationFn: (command: string) => submitHumanCommand(id, command),
  });
  // Plain-text chat to the agent (FR-17 Slice 4); queued server-side and read on
  // the agent's next turn. Separate mutation so its state is independent.
  const messageMutation = useMutation({
    mutationFn: (text: string) => submitMessage(id, text),
  });
  // The session record carries the free-launch + budget config (FR-17 Slice 5)
  // the WS event stream doesn't: max_steps/max_seconds are immutable, and the
  // *initial* free_launch seeds the derivation below. One fetch is enough — live
  // toggles arrive as `free_launch_changed` events, tracked by `currentFreeLaunch`.
  const { data: record } = useQuery({
    queryKey: ["retest-session", id],
    queryFn: () => getRetestSession(id),
  });
  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => setFreeLaunch(id, enabled),
  });
  // Human adjudication of a concluded session's verdict (FR-17 Slice 6a): Accept
  // records the agent's own call; Override records a different one. Either way it
  // appends a superseding operator verdict; the agent's record is never mutated.
  const adjudicateMutation = useMutation({
    mutationFn: (v: { status: string; rationale: string }) =>
      adjudicateSession(id, v.status, v.rationale),
  });
  const [overriding, setOverriding] = useState(false);
  const [overrideStatus, setOverrideStatus] = useState<VerdictStatus>("fixed");
  const [overrideRationale, setOverrideRationale] = useState("");

  const freeLaunch = currentFreeLaunch(events, record?.free_launch ?? false);
  const stepsDone = stepsUsed(events);
  const maxSteps = record?.max_steps ?? 8;
  const autoSeqs = autoApprovedSeqs(events);
  const terminalLines = toTerminalLines(events);
  const planSteps = currentPlan(events);
  const decisionSeq = lastDecisionSeq(events);
  // A pending approval is for either a command or a plan change; both gate on the
  // same tool_call_id, so they share the approve/reject mutations below.
  const latestProposal = [...events].reverse().find((event) => event.kind === "command_proposed");
  const awaitingApproval = status === "awaiting_command" && latestProposal !== undefined;

  // A concluded/given-up session carries an agent verdict the operator may
  // adjudicate. The panel closes once adjudicated — detected from the transcript
  // (a `verdict_adjudicated` event, present after a reload's WS replay) or from
  // the just-succeeded mutation (the WS stream is already closed at that point).
  const adjudicatedEvent = [...events].reverse().find((e) => e.kind === "verdict_adjudicated");
  const canAdjudicate = verdict !== null && (status === "concluded" || status === "given_up");
  const adjudicated = adjudicatedEvent !== undefined || adjudicateMutation.isSuccess;
  const finalVerdict = adjudicatedEvent?.payload ?? adjudicateMutation.variables;

  // Shared approve/reject block for a pending command or plan proposal.
  const renderApproval = (toolCallId: string, note: string) => (
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
        <span className="text-[11px] text-faint">{note}</span>
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
  );

  const trimmed = input.trim();
  const isCommand = trimmed.startsWith("!");
  const commandBody = isCommand ? trimmed.slice(1).trim() : "";
  const sessionOver = OVER_STATUSES.has(status);
  // `!command` runs in the sandbox (Slice 2); plain text is a chat message to the
  // agent (Slice 4). Both need non-empty content and a live session.
  const hasContent = isCommand ? commandBody.length > 0 : trimmed.length > 0;
  const canSubmit = hasContent && !sessionOver;

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
    if (event.kind === "human_message") {
      return [
        <HumanTurn
          key={event.seq}
          text={String(event.payload.text ?? "")}
          queued={event.seq > decisionSeq && !sessionOver}
        />,
      ];
    }
    if (event.kind === "command_proposed") {
      const isPending = awaitingApproval && event.seq === latestProposal?.seq;
      const wasAuto = autoSeqs.has(event.seq);
      return [
        <AgentTurn key={event.seq}>
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm text-dim">{String(event.payload.rationale ?? "")}</p>
            {wasAuto && (
              <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-iris-fg ring-1 ring-iris/30">
                auto
              </span>
            )}
          </div>
          <code className="mt-2 block overflow-x-auto rounded-md border border-line bg-ink/50 px-3 py-2 font-mono text-[13px] text-fg">
            <span className="text-faint">$</span> {String(event.payload.command ?? "")}
          </code>
          {isPending &&
            renderApproval(
              String(event.payload.tool_call_id),
              "runs once in the egress-locked sandbox",
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
          {/* Step budget meter — steps used / max (auto or human approvals). */}
          <span className="font-mono text-[12px] text-faint" aria-label="step budget">
            {budgetLabel(stepsDone, maxSteps)}
          </span>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-3">
            {/* Free-launch — auto-approve the agent's commands (plan changes stay gated). */}
            <label className="flex items-center gap-2 text-[13px] text-dim">
              <input
                type="checkbox"
                checked={freeLaunch}
                disabled={sessionOver || toggleMutation.isPending}
                onChange={(event) => {
                  toggleMutation.mutate(event.target.checked);
                }}
                className="accent-iris disabled:opacity-45"
              />
              Free-launch
            </label>
            <Button
              variant="ghost"
              disabled={endMutation.isPending}
              onClick={() => {
                endMutation.mutate();
              }}
            >
              End session
            </Button>
          </div>
          {toggleMutation.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(toggleMutation.error)}
            </p>
          )}
          {endMutation.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(endMutation.error)}
            </p>
          )}
        </div>
      </div>

      {/* Goal — the guiding checklist the agent works to (user-owned in 6b-ii). */}
      <Panel className="shrink-0">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <Eyebrow>Plan</Eyebrow>
          <span className="font-mono text-[11px] text-faint">
            {planSteps.length} {planSteps.length === 1 ? "step" : "steps"}
          </span>
        </div>
        <div className="p-4">
          {planSteps.length > 0 ? (
            <StepList steps={planSteps} />
          ) : (
            <p className="text-sm text-dim">No goal set yet.</p>
          )}
        </div>
      </Panel>

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
          {status === "given_up" ? (
            // The agent hit a budget bound (step or wall-clock). Rendered
            // distinctly from a reasoned verdict or an operator-ended session.
            <div role="alert" className="rounded-lg border border-warn/50 bg-warn/10 p-4">
              <Eyebrow>Agent gave up</Eyebrow>
              <p className="mt-1 text-sm text-warn-fg">
                {givenUpReason(events) ?? "budget exhausted"}
              </p>
            </div>
          ) : (
            verdict && (
              <div className="rounded-lg border border-line bg-panel-2/50 p-4">
                <div className="mb-2 flex items-center gap-2">
                  <Eyebrow>Verdict</Eyebrow>
                  {isKnownStatus(verdict.status) && <StatusBadge status={verdict.status} />}
                </div>
                <p className="text-sm text-fg">{verdict.rationale}</p>
              </div>
            )
          )}
          {canAdjudicate && verdict && (
            <div
              aria-label="adjudication"
              className="rounded-lg border border-line bg-panel-2/30 p-4"
            >
              <Eyebrow>Adjudication</Eyebrow>
              {adjudicated ? (
                <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-fg">
                  <span className="text-dim">Final verdict (operator):</span>
                  {typeof finalVerdict?.status === "string" &&
                    isKnownStatus(finalVerdict.status) && (
                      <StatusBadge status={finalVerdict.status} />
                    )}
                  {typeof finalVerdict?.rationale === "string" && finalVerdict.rationale && (
                    <span>— {finalVerdict.rationale}</span>
                  )}
                </p>
              ) : (
                <>
                  <p className="mt-1 text-xs text-dim">
                    Accept the agent&rsquo;s verdict, or override it with your own determination.
                  </p>
                  {overriding ? (
                    <div className="mt-2 space-y-2">
                      <select
                        aria-label="override status"
                        value={overrideStatus}
                        onChange={(e) => {
                          setOverrideStatus(e.target.value as VerdictStatus);
                        }}
                        className="rounded border border-line bg-panel px-2 py-1 text-sm text-fg"
                      >
                        {VERDICT_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {STATUS_META[s].label}
                          </option>
                        ))}
                      </select>
                      <textarea
                        aria-label="override rationale"
                        value={overrideRationale}
                        onChange={(e) => {
                          setOverrideRationale(e.target.value);
                        }}
                        placeholder="Why you override the agent's verdict…"
                        className="w-full rounded border border-line bg-panel px-2 py-1 text-sm text-fg"
                        rows={2}
                      />
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="accent"
                          disabled={adjudicateMutation.isPending}
                          onClick={() => {
                            adjudicateMutation.mutate({
                              status: overrideStatus,
                              rationale: overrideRationale,
                            });
                          }}
                        >
                          Submit override
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            setOverriding(false);
                          }}
                        >
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button
                        variant="positive"
                        disabled={adjudicateMutation.isPending}
                        onClick={() => {
                          adjudicateMutation.mutate({
                            status: verdict.status,
                            rationale: verdict.rationale,
                          });
                        }}
                      >
                        Accept
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setOverriding(true);
                        }}
                      >
                        Override…
                      </Button>
                    </div>
                  )}
                  {adjudicateMutation.isError && (
                    <p className="mt-2 text-xs text-danger">
                      {errorMessage(adjudicateMutation.error)}
                    </p>
                  )}
                </>
              )}
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

      {/* Operator console: plain text messages the agent; `!<command>` runs it. */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!canSubmit) return;
          if (isCommand) humanCommandMutation.mutate(commandBody);
          else messageMutation.mutate(trimmed);
          setInput("");
        }}
        className="shrink-0"
      >
        <div className="flex items-center gap-2 rounded-lg border border-line bg-panel/60 px-3 py-2">
          <input
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
            }}
            placeholder="Message the agent — or !command to run it in the sandbox"
            disabled={sessionOver}
            aria-label="Operator console input"
            className="min-w-0 flex-1 bg-transparent font-mono text-[13px] text-fg outline-none placeholder:text-faint disabled:opacity-45"
          />
          <Button type="submit" variant="ghost" disabled={!canSubmit}>
            {isCommand ? "Run" : "Send"}
          </Button>
        </div>
        {!sessionOver && (
          <p className="mt-1 px-1 text-[11px] text-faint">
            {isCommand ? (
              <>Runs once in the egress-locked sandbox.</>
            ) : (
              <>Messages are read on the agent&apos;s next turn — approve or reject a pending step to deliver now.</>
            )}
          </p>
        )}
        {humanCommandMutation.isError && (
          <p role="alert" className="mt-1 px-1 text-sm text-danger-fg">
            {errorMessage(humanCommandMutation.error)}
          </p>
        )}
        {messageMutation.isError && (
          <p role="alert" className="mt-1 px-1 text-sm text-danger-fg">
            {errorMessage(messageMutation.error)}
          </p>
        )}
      </form>
    </div>
  );
}
