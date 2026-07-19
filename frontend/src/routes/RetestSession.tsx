import { useEffect, useRef, useState, type ReactNode } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import {
  adjudicateSession,
  approveCommand,
  concludeSession,
  continueSession,
  endRetestSession,
  getRetestSession,
  regenerateSessionGoal,
  rejectCommand,
  setFreeLaunch,
  setSessionGoal,
  startRetestSession,
  submitHumanCommand,
  submitMessage,
  type SessionEvent,
} from "../api/client";
import { RetestTerminal } from "../components/RetestTerminal";
import { StatusBadge } from "../components/StatusBadge";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { queryKeys } from "../hooks/queryKeys";
import { useRetestSession } from "../hooks/useRetestSession";
import { errorMessage } from "../lib/format";
import {
  autoApprovedSeqs,
  currentFreeLaunch,
  givenUpReason,
  guidanceReason,
} from "../lib/sessionDerivations";
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

/** The retest scope = endpoints from the launch-time `target_set` event (read-only). */
function currentTarget(events: SessionEvent[]): string[] {
  const latest = [...events].reverse().find((event) => event.kind === "target_set");
  return latest && Array.isArray(latest.payload.endpoints)
    ? latest.payload.endpoints.map(String)
    : [];
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

/** Statuses where the agent is computing its next turn (an LLM call is in flight). */
const THINKING_STATUSES = new Set(["starting", "thinking", "running_command"]);

/** Whether the agent is actively working — drives the live "thinking" indicator. */
function isThinking(status: string): boolean {
  return THINKING_STATUSES.has(status);
}

/** A short, user-facing label for the session's lifecycle status. */
function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    starting: "Working",
    thinking: "Working",
    running_command: "Working",
    awaiting_command: "Awaiting your approval",
    needs_guidance: "Paused — needs you",
    concluded: "Concluded",
    given_up: "Ended",
    ended: "Ended",
    error: "Error",
  };
  return labels[status] ?? status.replace(/_/g, " ");
}

/**
 * A live "the agent is thinking" bubble: three dots gently pulsing in sequence.
 * Shown while an LLM call is in flight (local models can take a while), so a slow
 * turn reads as working rather than frozen.
 */
function ThinkingBubble() {
  return (
    <div className="flex gap-3" aria-label="agent thinking">
      <span
        className="mt-1.5 h-2 w-2 shrink-0 animate-pulse rounded-full bg-iris shadow-[0_0_8px_var(--color-iris)]"
        aria-hidden
      />
      <div className="flex items-center gap-1.5 rounded-lg border border-line bg-panel-2/50 px-4 py-3">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-dim"
            style={{ animationDelay: `${String(delay)}ms` }}
          />
        ))}
      </div>
    </div>
  );
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
export function RetestSession({
  sessionId,
  embedded = false,
}: {
  sessionId: number;
  /** True when rendered inside the finding-stage wizard (its header + pipeline sit
   * above), so the cockpit reserves more height than on the standalone route. */
  embedded?: boolean;
}) {
  const id = sessionId;
  const { events, status, verdict } = useRetestSession(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [terminalOpen, setTerminalOpen] = useState(true);
  const [input, setInput] = useState("");
  const [command, setCommand] = useState("");
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
  // Restart abandons this attempt and opens a fresh session for the same finding,
  // seeded with the current goal so the operator keeps their framing. The old
  // session is ended first to free its sandbox; then the finding's session list is
  // refreshed and the view follows the new session (the finding stage renders its
  // newest session).
  const restartMutation = useMutation({
    mutationFn: ({ findingId, goal }: { findingId: number; goal: string[] }) =>
      endRetestSession(id).then(() =>
        startRetestSession(findingId, goal.length > 0 ? { initial_goal: goal } : undefined),
      ),
    onSuccess: async (_fresh, { findingId }) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.findingSessions(findingId) });
      navigate(`/findings/${String(findingId)}/retest`);
    },
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
  // The session record carries the *initial* free-launch mode (FR-17 Slice 5) that
  // the WS event stream doesn't, seeding the derivation below. One fetch is enough —
  // live toggles arrive as `free_launch_changed` events, tracked by `currentFreeLaunch`.
  const { data: record } = useQuery({
    queryKey: queryKeys.retestSession(id),
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
  // The user-owned goal (FR-17 6b-ii): edit the steps as text (one per line) or
  // regenerate them via the LLM. Both deliver to the agent on its next turn.
  const goalMutation = useMutation({
    mutationFn: (steps: string[]) => setSessionGoal(id, steps),
  });
  const regenerateGoalMutation = useMutation({
    mutationFn: () => regenerateSessionGoal(id),
  });
  const [editingGoal, setEditingGoal] = useState(false);
  const [goalDraft, setGoalDraft] = useState("");
  // Pause-and-ask (ADR-0034): when the session is paused for guidance, Keep going
  // raises the budget and resumes; Conclude records the operator's determination.
  const continueMutation = useMutation({
    mutationFn: () => continueSession(id),
  });
  const concludeMutation = useMutation({
    mutationFn: (v: { status: string; rationale: string }) =>
      concludeSession(id, v.status, v.rationale),
  });
  const [concluding, setConcluding] = useState(false);
  const [concludeStatus, setConcludeStatus] = useState<VerdictStatus>("inconclusive");
  const [concludeRationale, setConcludeRationale] = useState("");

  const freeLaunch = currentFreeLaunch(events, record?.free_launch ?? false);
  const autoSeqs = autoApprovedSeqs(events);
  const terminalLines = toTerminalLines(events);
  const planSteps = currentPlan(events);
  const targetEndpoints = currentTarget(events);
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
  const commandTrimmed = command.trim();
  const sessionOver = OVER_STATUSES.has(status);
  const findingId = record?.finding_id ?? null;
  // The composer now sends chat messages only (Slice 4); operator commands live in
  // the terminal's own prompt (they run in the sandbox and the agent observes them,
  // Slice 2). Each needs non-empty content and a session that hasn't ended.
  const canSendMessage = trimmed.length > 0 && !sessionOver;
  const canRunCommand = commandTrimmed.length > 0 && !sessionOver;

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
            {wasAuto && <span className="shrink-0 text-[11px] text-faint">ran automatically</span>}
          </div>
          <code className="mt-2 block overflow-x-auto rounded-md border border-line bg-ink/50 px-3 py-2 font-mono text-[13px] text-fg">
            <span className="text-faint">$</span> {String(event.payload.command ?? "")}
          </code>
          {isPending &&
            renderApproval(
              String(event.payload.tool_call_id),
              "Runs once in the isolated sandbox.",
            )}
        </AgentTurn>,
      ];
    }
    if (event.kind === "command_rejected") {
      const reason = String(event.payload.reason ?? "");
      return [
        <p key={event.seq} className="pl-5 text-[12px] text-faint">
          Command declined{reason ? `: ${reason}` : ""}
        </p>,
      ];
    }
    return [];
  });

  return (
    <div
      className={`flex flex-col gap-3 ${
        embedded ? "min-h-[calc(100dvh-20rem)]" : "min-h-[calc(100dvh-9rem)]"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {/* Live status dot: pulses while the agent works, steady otherwise. */}
          <span
            className={`h-2 w-2 rounded-full ${
              isThinking(status)
                ? "animate-pulse bg-iris shadow-[0_0_8px_var(--color-iris)]"
                : sessionOver
                  ? "bg-faint"
                  : "bg-ok"
            }`}
            aria-hidden
          />
          <span className="text-[13px] font-medium text-fg">{statusLabel(status)}</span>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-3">
            {/* Auto-run — approve the agent's commands automatically (plan changes stay gated). */}
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
              Auto-run
            </label>
            <Button
              variant="ghost"
              disabled={findingId === null || restartMutation.isPending}
              onClick={() => {
                if (findingId !== null) restartMutation.mutate({ findingId, goal: planSteps });
              }}
            >
              Restart
            </Button>
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
          {restartMutation.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(restartMutation.error)}
            </p>
          )}
        </div>
      </div>

      {/* main: the goal (full width, right below the stages bar) then the boxed chat */}
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        {/* Current goal — the user-owned checklist, full width below the stages bar (FR-17 6b-ii). */}
        <Panel className="shrink-0">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <Eyebrow>Current goal</Eyebrow>
          </div>
          <div className="space-y-3 p-4">
            {targetEndpoints.length > 0 && (
              <div className="rounded-lg border border-line bg-panel-2/40 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <Eyebrow>Scope</Eyebrow>
                  <span className="text-[11px] text-faint">set at launch — Restart to change</span>
                </div>
                <ul className="mt-1.5 space-y-0.5">
                  {targetEndpoints.map((ep) => (
                    <li key={ep} className="break-all font-mono text-[12px] text-fg">
                      {ep}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {editingGoal ? (
              <div className="space-y-2">
                <textarea
                  aria-label="goal steps"
                  value={goalDraft}
                  onChange={(e) => {
                    setGoalDraft(e.target.value);
                  }}
                  rows={4}
                  className="w-full rounded border border-line bg-panel px-2 py-1 font-mono text-[13px] text-fg"
                  placeholder="One step per line…"
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="accent"
                    disabled={goalMutation.isPending}
                    onClick={() => {
                      const steps = goalDraft
                        .split("\n")
                        .map((s) => s.trim())
                        .filter(Boolean);
                      goalMutation.mutate(steps);
                      setEditingGoal(false);
                    }}
                  >
                    Save goal
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setEditingGoal(false);
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                {planSteps.length > 0 ? (
                  <StepList steps={planSteps} />
                ) : (
                  <p className="text-sm text-dim">No goal set yet.</p>
                )}
                {!sessionOver && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setGoalDraft(planSteps.join("\n"));
                        setEditingGoal(true);
                      }}
                    >
                      Edit goal
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={regenerateGoalMutation.isPending}
                      onClick={() => {
                        regenerateGoalMutation.mutate();
                      }}
                    >
                      {regenerateGoalMutation.isPending ? "Regenerating…" : "Regenerate goal"}
                    </Button>
                  </div>
                )}
                {regenerateGoalMutation.isError && (
                  <p role="alert" className="text-sm text-danger-fg">
                    {errorMessage(regenerateGoalMutation.error)}
                  </p>
                )}
              </>
            )}
          </div>
        </Panel>

        {/* Conversation — a boxed chat with the agent, panelled like the rest. A
            min-height keeps it usable so the docked terminal can never squeeze the
            approval gate to nothing on a short viewport (the page scrolls instead). */}
        <Panel className="flex min-h-[16rem] flex-1 flex-col overflow-hidden">
          <PanelHeader eyebrow="Conversation" />
          <div
            ref={chatRef}
            role="log"
            aria-label="Agent conversation"
            className="min-h-0 flex-1 overflow-y-auto p-4"
          >
            <div className="mx-auto flex max-w-[68rem] flex-col gap-3 pb-1">
            {chatItems.length === 0 && !verdict && !isThinking(status) && (
              <p className="text-sm text-dim">Starting the sandboxed retest…</p>
            )}
            {chatItems}
            {isThinking(status) && !awaitingApproval && !verdict && <ThinkingBubble />}
            {status === "needs_guidance" ? (
              // Paused for guidance (ADR-0034): the agent handed back after
              // exhausting its options. The operator steers (chat/commands below)
              // and keeps going, or concludes the retest themselves. Sandbox alive.
              <div
                aria-label="needs guidance"
                className="space-y-3 rounded-lg border border-iris/50 bg-iris/10 p-4"
              >
                <div>
                  <Eyebrow>Paused — needs your guidance</Eyebrow>
                  <p className="mt-1 text-sm text-fg">
                    {guidanceReason(events) ?? "The agent asked for your guidance."}
                  </p>
                  <p className="mt-1 text-xs text-dim">
                    Steer it with a message or a command below, then keep going — or conclude the
                    retest yourself.
                  </p>
                </div>
                {concluding ? (
                  <div className="space-y-2">
                    <select
                      aria-label="conclude status"
                      value={concludeStatus}
                      onChange={(e) => {
                        setConcludeStatus(e.target.value as VerdictStatus);
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
                      aria-label="conclude rationale"
                      value={concludeRationale}
                      onChange={(e) => {
                        setConcludeRationale(e.target.value);
                      }}
                      placeholder="Your determination and why…"
                      rows={2}
                      className="w-full rounded border border-line bg-panel px-2 py-1 text-sm text-fg"
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="accent"
                        disabled={concludeMutation.isPending}
                        onClick={() => {
                          concludeMutation.mutate({
                            status: concludeStatus,
                            rationale: concludeRationale,
                          });
                        }}
                      >
                        Record verdict
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setConcluding(false);
                        }}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="accent"
                      disabled={continueMutation.isPending}
                      onClick={() => {
                        continueMutation.mutate();
                      }}
                    >
                      Keep going
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setConcluding(true);
                      }}
                    >
                      Conclude…
                    </Button>
                  </div>
                )}
                {continueMutation.isError && (
                  <p role="alert" className="text-xs text-danger-fg">
                    {errorMessage(continueMutation.error)}
                  </p>
                )}
                {concludeMutation.isError && (
                  <p role="alert" className="text-xs text-danger-fg">
                    {errorMessage(concludeMutation.error)}
                  </p>
                )}
              </div>
            ) : status === "given_up" ? (
              // Legacy: sessions from before ADR-0034 could reach a terminal
              // give-up. New sessions pause for guidance instead.
              <div role="alert" className="rounded-lg border border-warn/50 bg-warn/10 p-4">
                <Eyebrow>Retest ended</Eyebrow>
                <p className="mt-1 text-sm text-warn-fg">
                  {givenUpReason(events) ?? "The agent stopped without a determination."}
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
        </Panel>

      </div>

      {/* Composer — a chat message to the agent, read on its next turn (Slice 4). */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!canSendMessage) return;
          messageMutation.mutate(trimmed);
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
            placeholder="Message the agent…"
            disabled={sessionOver}
            aria-label="Message the agent"
            className="min-w-0 flex-1 bg-transparent text-[14px] text-fg outline-none placeholder:text-faint disabled:opacity-45"
          />
          <Button type="submit" variant="accent" disabled={!canSendMessage}>
            Send
          </Button>
        </div>
        {!sessionOver && (
          <p className="mt-1 px-1 text-[11px] text-faint">
            The agent replies here; your message also steers its next turn.
          </p>
        )}
        {messageMutation.isError && (
          <p role="alert" className="mt-1 px-1 text-sm text-danger-fg">
            {errorMessage(messageMutation.error)}
          </p>
        )}
      </form>

      {/* Terminal — docked below the conversation: executed output plus your own
          prompt. A command you run here executes once in the isolated sandbox and
          the agent observes it on its next turn, as if it had run it itself. */}
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
          <div className="space-y-2 p-3">
            <RetestTerminal lines={terminalLines} />
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!canRunCommand) return;
                humanCommandMutation.mutate(commandTrimmed);
                setCommand("");
              }}
            >
              <div className="flex items-center gap-2 rounded-md border border-line bg-ink/50 px-3 py-2 font-mono text-[13px]">
                <span className="shrink-0 select-none text-iris-fg">operator$</span>
                <input
                  value={command}
                  onChange={(event) => {
                    setCommand(event.target.value);
                  }}
                  placeholder={sessionOver ? "Sandbox closed" : "Run a command in the sandbox…"}
                  disabled={sessionOver}
                  aria-label="Terminal command input"
                  className="min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-faint disabled:opacity-45"
                />
                <Button type="submit" variant="ghost" disabled={!canRunCommand}>
                  Run
                </Button>
              </div>
              {humanCommandMutation.isError && (
                <p role="alert" className="mt-1 text-sm text-danger-fg">
                  {errorMessage(humanCommandMutation.error)}
                </p>
              )}
            </form>
          </div>
        )}
      </Panel>
    </div>
  );
}
