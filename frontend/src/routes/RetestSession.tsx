import { useEffect, useRef, useState, type ReactNode } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import {
  adjudicateSession,
  approveCommand,
  concludeSession,
  endRetestSession,
  getRetestSession,
  regenerateSessionGoal,
  rejectCommand,
  restartModel,
  setFreeLaunch,
  setSessionGoal,
  startRetestSession,
  stopSession,
  submitHumanCommand,
  submitMessage,
  type SessionEvent,
} from "../api/client";
import { RetestTerminal } from "../components/RetestTerminal";
import { StatusBadge } from "../components/StatusBadge";
import {
  AlertIcon,
  CheckIcon,
  CrossIcon,
  ExitIcon,
  FlagIcon,
  GoalIcon,
  PauseIcon,
  PencilIcon,
  PowerIcon,
  RestartIcon,
  SendIcon,
  TargetIcon,
  TerminalIcon,
  VerdictIcon,
} from "../components/icons";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { queryKeys } from "../hooks/queryKeys";
import { useRetestSession } from "../hooks/useRetestSession";
import { goalStepsToText, parseGoalSteps } from "../lib/goal";
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

/** Statuses where the agent is actively working, so the operator can Stop it (#150). */
const RUNNING_STATUSES = new Set(["starting", "thinking", "awaiting_command", "running_command"]);

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
    idle: "Not started",
    starting: "Working",
    thinking: "Working",
    running_command: "Working",
    awaiting_command: "Awaiting your approval",
    needs_guidance: "Paused — needs you",
    awaiting_operator: "Waiting for you",
    stopped: "Paused by you",
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
/**
 * The turn-in-flight indicator. Shows the model's live reasoning when it streams
 * any (issue #140), and falls back to the bouncing dots when it doesn't — some
 * backends emit nothing until the whole turn lands, and an empty box would read
 * as a stall.
 *
 * The reasoning is deliberately styled as secondary, muted text: it is the
 * model's scratch work, not a claim, and it vanishes the moment the turn's real
 * event arrives. Nothing here is part of the audit trail.
 */
function ThinkingBubble({ reasoning }: { reasoning: string }) {
  return (
    <div className="flex gap-3" aria-label="agent thinking">
      <span
        className="mt-1.5 h-2 w-2 shrink-0 animate-pulse rounded-full bg-iris shadow-[0_0_8px_var(--color-iris)]"
        aria-hidden
      />
      {reasoning ? (
        <div className="min-w-0 rounded-lg border border-line bg-panel-2/50 px-4 py-3">
          <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-faint">
            Thinking
          </p>
          {/* Only the tail: the reasoning can run to hundreds of tokens and the
              operator is watching progress, not reading an essay. */}
          <p className="max-h-32 overflow-hidden whitespace-pre-wrap text-[13px] leading-relaxed text-dim">
            {reasoning.slice(-600)}
          </p>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 rounded-lg border border-line bg-panel-2/50 px-4 py-3">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-dim"
              style={{ animationDelay: `${String(delay)}ms` }}
            />
          ))}
        </div>
      )}
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
        {queued && <p className="mt-1 text-[11px] text-faint">queued</p>}
      </div>
    </div>
  );
}

/**
 * Seq of the latest `messages_delivered` marker (#204): the agent emits one each
 * turn it drains queued operator messages, so a `human_message` with a *higher*
 * seq is still waiting to be read, and any below it has been delivered. This is
 * what clears the "queued" hint the moment the agent actually has the message —
 * on any resume path, not only approve/reject.
 */
function latestDeliveredSeq(events: SessionEvent[]): number {
  const latest = [...events].reverse().find((event) => event.kind === "messages_delivered");
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
  const { events, status, verdict, thinking } = useRetestSession(id);
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
  // seeded with the current goal + scope so the operator keeps their framing. The
  // fresh session is opened **deferred** (issue #150): it lands `idle` and waits for
  // an explicit Start rather than auto-running. The old session is ended first to
  // free its sandbox; then the finding's session list is refreshed and the view
  // follows the new session (the finding stage renders its newest session).
  const restartMutation = useMutation({
    mutationFn: ({ findingId, goal, scope }: { findingId: number; goal: string[]; scope: string[] }) =>
      endRetestSession(id).then(() =>
        startRetestSession(findingId, {
          deferred: true,
          ...(goal.length > 0 ? { initial_goal: goal } : {}),
          ...(scope.length > 0 ? { target_endpoints: scope } : {}),
        }),
      ),
    onSuccess: async (_fresh, { findingId }) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.findingSessions(findingId) });
      navigate(`/findings/${String(findingId)}/retest`);
    },
  });
  // Stop pauses a running session, keeping its sandbox alive (issue #150). It has
  // no Start/Resume counterpart in the UI: the server wakes a parked session when
  // the operator messages it (#163), so `POST …/message` is the only resume path
  // the console needs. The lifecycle routes themselves remain as programmatic API.
  const stopMutation = useMutation({ mutationFn: () => stopSession(id) });
  // Restart-model aborts the in-flight turn and re-runs it to unstick a wedged
  // model (issue #204) — distinct from Restart (a fresh session). Session, sandbox,
  // goal and history are all kept; only the frozen turn is thrown away and retried.
  const restartModelMutation = useMutation({ mutationFn: () => restartModel(id) });
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
  // Pause-and-ask (ADR-0034): at a guidance pause the operator either replies —
  // which resumes the agent server-side (#163) — or concludes the retest here.
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
  const deliveredSeq = latestDeliveredSeq(events);
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
          <CheckIcon />
          Approve
        </Button>
        <Button
          variant="danger"
          disabled={rejectMutation.isPending}
          onClick={() => {
            rejectMutation.mutate(toolCallId);
          }}
        >
          <CrossIcon />
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
  // Lifecycle-derived flags (issue #150). `idle` = created but not started (no
  // sandbox); `stopped` = operator-paused (sandbox alive). `sandboxLive` gates the
  // actions that need a running sandbox — `!` commands, Conclude, Stop.
  const isIdle = status === "idle";
  const isStopped = status === "stopped";
  const isRunning = RUNNING_STATUSES.has(status);
  const sandboxLive = !sessionOver && !isIdle;
  const findingId = record?.finding_id ?? null;
  // The composer sends chat messages only (Slice 4); operator commands live in the
  // terminal's own prompt (they run in the sandbox and the agent observes them,
  // Slice 2). A `!` command still needs a live sandbox — but a *message* does not:
  // the chat is the lifecycle control (#163), so messaging an `idle` session is
  // exactly how it gets provisioned and started. Only a session that is over
  // (concluded/ended/error) has nobody left to talk to.
  const canSendMessage = trimmed.length > 0 && !sessionOver;
  const canRunCommand = commandTrimmed.length > 0 && sandboxLive;

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
          queued={event.seq > deliveredSeq && !sessionOver}
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
          {typeof event.payload.timeout_seconds === "number" && (
            <p className="mt-1 text-[11px] text-faint">
              runs up to {String(event.payload.timeout_seconds)}s before it is stopped
            </p>
          )}
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
    // Viewport-*minimum* flex column (#204/#206): the console is at least tall
    // enough to fill the viewport, so the conversation grows to fill the space
    // between the goal (top) and the docked terminal (bottom) instead of floating.
    // Crucially it is a `min-h`, not a fixed `h`: when the terminal is expanded and
    // goal + terminal + conversation exceed the viewport, the column *grows* and the
    // page scrolls — rather than a fixed height crushing the flex-1 conversation to
    // nothing and letting the terminal overlap it (the #206 bug). The conversation
    // panel keeps its own min-height floor so it is always usable. The embedded
    // finding stage reserves more chrome above (identity + pipeline).
    <div
      className={`flex flex-col gap-3 ${
        embedded ? "min-h-[calc(100dvh-19rem)]" : "min-h-[calc(100dvh-8rem)]"
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
            {/* Auto-run — approve the agent's commands automatically (plan changes stay gated).
                Only meaningful while the sandbox is live (running or stopped). */}
            {sandboxLive && (
              <label className="flex items-center gap-2 text-[13px] text-dim">
                <input
                  type="checkbox"
                  checked={freeLaunch}
                  disabled={toggleMutation.isPending}
                  onChange={(event) => {
                    toggleMutation.mutate(event.target.checked);
                  }}
                  className="accent-iris disabled:opacity-45"
                />
                Auto-run
              </label>
            )}
            {/* Lifecycle controls, each shown only when it makes sense (#150).
                Stop is the only halt control: there is no Start/Resume counterpart
                because *talking to* a parked agent is what starts or continues it
                (#163). One direction is a button, the other is the conversation. */}
            {isRunning && (
              <Button
                variant="ghost"
                disabled={stopMutation.isPending}
                onClick={() => {
                  stopMutation.mutate();
                }}
              >
                <PauseIcon />
                Stop
              </Button>
            )}
            {/* Restart model — abort a wedged turn and re-run it (#204). Only while a
                turn is actually in flight (thinking); at the gate there is nothing to
                unstick. Distinct from Restart below, which opens a fresh session. */}
            {isThinking(status) && (
              <Button
                variant="ghost"
                title="Abort the current turn and re-run it — use if the model is stuck"
                disabled={restartModelMutation.isPending}
                onClick={() => {
                  restartModelMutation.mutate();
                }}
              >
                <PowerIcon />
                Restart model
              </Button>
            )}
            {/* Conclude is reachable from any live state (#150). In needs_guidance the
                pause banner carries its own Conclude button, so skip it here to keep
                a single entry point. */}
            {sandboxLive && !concluding && status !== "needs_guidance" && (
              <Button
                variant="ghost"
                onClick={() => {
                  setConcluding(true);
                }}
              >
                <FlagIcon />
                Conclude…
              </Button>
            )}
            <Button
              variant="ghost"
              disabled={findingId === null || restartMutation.isPending}
              onClick={() => {
                if (findingId !== null)
                  restartMutation.mutate({ findingId, goal: planSteps, scope: targetEndpoints });
              }}
            >
              <RestartIcon />
              Restart
            </Button>
            {!sessionOver && (
              <Button
                variant="ghost"
                disabled={endMutation.isPending}
                onClick={() => {
                  endMutation.mutate();
                }}
              >
                <ExitIcon />
                End session
              </Button>
            )}
          </div>
          {[
            toggleMutation.error,
            endMutation.error,
            restartMutation.error,
            stopMutation.error,
            restartModelMutation.error,
          ]
            .filter(Boolean)
            .map((err, i) => (
              <p key={i} role="alert" className="text-sm text-danger-fg">
                {errorMessage(err)}
              </p>
            ))}
        </div>
      </div>

      {/* main: the goal (full width, right below the stages bar) then the boxed chat.
          `flex-1 min-h-0` so it claims the height left by the header + terminal and
          passes it down to the conversation, which flexes to fill it (#204). */}
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        {/* Current goal — the user-owned checklist, full width below the stages bar (FR-17 6b-ii). */}
        <Panel className="shrink-0">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="flex items-center gap-2 text-faint">
              <GoalIcon />
              <Eyebrow>Current goal</Eyebrow>
            </span>
          </div>
          <div className="space-y-3 p-4">
            {targetEndpoints.length > 0 && (
              <div className="rounded-lg border border-line bg-panel-2/40 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 text-faint">
                    <TargetIcon />
                    <Eyebrow>Scope</Eyebrow>
                  </span>
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
                      const steps = parseGoalSteps(goalDraft);
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
                        setGoalDraft(goalStepsToText(planSteps));
                        setEditingGoal(true);
                      }}
                    >
                      <PencilIcon />
                      Edit goal
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={regenerateGoalMutation.isPending}
                      onClick={() => {
                        regenerateGoalMutation.mutate();
                      }}
                    >
                      <RestartIcon />
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

        {/* Conversation — one chat panel (#157): the scrolling transcript with the
            composer welded to its bottom edge, so the agent's turns, the operator's
            turns, the gate, and the box you type into all read as a single thread
            rather than a stack of disconnected boxes. */}
        {/* `flex-1` grows the panel to fill the space its parent hands down (#204),
            while a real `min-h` floor (#206) stops it collapsing when the terminal
            is expanded — a crushed panel let the terminal overlap it. Its inner
            `overflow-y-auto` keeps the transcript scrolling with the composer welded
            to the bottom edge; the page scrolls only once even this floor + the
            terminal exceed the viewport. */}
        <Panel className="flex min-h-[18rem] flex-1 flex-col overflow-hidden">
          <PanelHeader eyebrow="Conversation" />
          <div
            ref={chatRef}
            role="log"
            aria-label="Agent conversation"
            className="min-h-0 flex-1 overflow-y-auto p-4"
          >
            <div className="flex w-full flex-col gap-3 pb-1">
            {/* An idle session has run nothing — no sandbox, no LLM call. It wakes
                when you talk to it (#163): the first message provisions the
                egress-locked sandbox and becomes the opening turn's steer, so the
                agent never starts unbidden and there is nothing extra to press. */}
            {isIdle && (
              <div
                aria-label="asleep"
                className="flex flex-col items-center gap-2 rounded-lg border border-line bg-panel-2/40 px-4 py-8 text-center"
              >
                <span className="flex items-center gap-2 text-faint">
                  <PowerIcon />
                  <Eyebrow>Agent asleep</Eyebrow>
                </span>
                <p className="max-w-sm text-sm text-dim">
                  The goal and scope are set, but nothing has run yet. Send a message below to wake
                  it — that provisions the sandbox and becomes its first instruction.
                </p>
              </div>
            )}
            {chatItems.length === 0 && !verdict && !isThinking(status) && !isIdle && (
              <p className="text-sm text-dim">Starting the sandboxed retest…</p>
            )}
            {chatItems}
            {isThinking(status) && !awaitingApproval && !verdict && (
              <ThinkingBubble reasoning={thinking} />
            )}
            {isStopped && (
              <div
                aria-label="stopped"
                className="rounded-lg border border-line bg-panel-2/50 p-4 text-sm text-dim"
              >
                <span className="flex items-center gap-2 text-faint">
                  <PauseIcon />
                  <Eyebrow>Paused by you</Eyebrow>
                </span>
                <p className="mt-1">
                  The sandbox is kept alive. Message the agent to pick up where it left off, or
                  conclude the retest yourself.
                </p>
              </div>
            )}
            {status === "needs_guidance" ? (
              // Paused for guidance (ADR-0034): the agent handed back after
              // exhausting its options. The operator steers (chat/commands below)
              // and keeps going, or concludes the retest themselves. Sandbox alive.
              <div
                aria-label="needs guidance"
                className="space-y-3 rounded-lg border border-iris/50 bg-iris/10 p-4"
              >
                <div>
                  <span className="flex items-center gap-2 text-iris-fg">
                    <AlertIcon />
                    <Eyebrow>Paused — needs your guidance</Eyebrow>
                  </span>
                  <p className="mt-1 text-sm text-fg">
                    {guidanceReason(events) ?? "The agent asked for your guidance."}
                  </p>
                  <p className="mt-1 text-xs text-dim">
                    Reply below to keep it going — your message is the steer. Or conclude the retest
                    yourself.
                  </p>
                </div>
                {/* No "Keep going" button: replying *is* keeping going (#163). The
                    Conclude form renders further down the thread (#157). */}
                {!concluding && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setConcluding(true);
                      }}
                    >
                      <FlagIcon />
                      Conclude…
                    </Button>
                  </div>
                )}
              </div>
            ) : status === "given_up" ? (
              // Legacy: sessions from before ADR-0034 could reach a terminal
              // give-up. New sessions pause for guidance instead.
              <div role="alert" className="rounded-lg border border-warn/50 bg-warn/10 p-4">
                <span className="flex items-center gap-2 text-warn-fg">
                  <AlertIcon />
                  <Eyebrow>Retest ended</Eyebrow>
                </span>
                <p className="mt-1 text-sm text-warn-fg">
                  {givenUpReason(events) ?? "The agent stopped without a determination."}
                </p>
              </div>
            ) : (
              verdict && (
                <div className="rounded-lg border border-line bg-panel-2/50 p-4">
                  <div className="mb-2 flex items-center gap-2 text-faint">
                    <VerdictIcon />
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
                <span className="flex items-center gap-2 text-faint">
                  <VerdictIcon />
                  <Eyebrow>Adjudication</Eyebrow>
                </span>
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
                          <CheckIcon />
                          Accept
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            setOverriding(true);
                          }}
                        >
                          <PencilIcon />
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
            {/* Conclude — the operator writes their own verdict, available at any
                live point in the retest (#150). It renders as the newest item in
                the thread (#157) rather than a detached panel above it, so the
                form appears where the conversation left off. */}
            {concluding && sandboxLive && (
              <div
                aria-label="conclude"
                className="space-y-3 rounded-lg border border-iris/50 bg-iris/5 p-4"
              >
                <div>
                  <span className="flex items-center gap-2 text-iris-fg">
                    <FlagIcon />
                    <Eyebrow>Conclude the retest yourself</Eyebrow>
                  </span>
                  <p className="mt-1 text-xs text-dim">
                    Record your own determination. The sandbox is torn down and this becomes the
                    session&rsquo;s verdict.
                  </p>
                </div>
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
                    <VerdictIcon />
                    Record verdict
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setConcluding(false);
                    }}
                  >
                    <CrossIcon />
                    Cancel
                  </Button>
                </div>
                {concludeMutation.isError && (
                  <p role="alert" className="text-xs text-danger-fg">
                    {errorMessage(concludeMutation.error)}
                  </p>
                )}
              </div>
            )}
            </div>
          </div>

          {/* Composer — welded to the thread's bottom edge inside the same panel
              (#157), the way a chat app docks its input. A message is read by the
              agent on its next turn (Slice 4). */}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!canSendMessage) return;
              messageMutation.mutate(trimmed);
              setInput("");
            }}
            className="shrink-0 border-t border-line bg-panel-2/30 px-3 py-2.5"
          >
            <div className="flex items-center gap-2 rounded-lg border border-line bg-panel/60 px-3 py-2">
              <input
                value={input}
                onChange={(event) => {
                  setInput(event.target.value);
                }}
                placeholder={
                  isIdle
                    ? "Tell the agent to start…"
                    : isStopped || status === "needs_guidance"
                      ? "Reply to pick it back up…"
                      : "Message the agent…"
                }
                disabled={sessionOver}
                aria-label="Message the agent"
                className="min-w-0 flex-1 bg-transparent text-[14px] text-fg outline-none placeholder:text-faint disabled:opacity-45"
              />
              <Button type="submit" variant="accent" disabled={!canSendMessage}>
                <SendIcon />
                Send
              </Button>
            </div>
            {messageMutation.isError && (
              <p role="alert" className="mt-1 px-1 text-sm text-danger-fg">
                {errorMessage(messageMutation.error)}
              </p>
            )}
          </form>
        </Panel>

      </div>

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
          <span className="flex items-center gap-2 text-faint">
            <TerminalIcon />
            <Eyebrow>Terminal</Eyebrow>
          </span>
          <span className="font-mono text-[11px] text-faint">
            {terminalLines.length} {terminalLines.length === 1 ? "line" : "lines"}{" "}
            {terminalOpen ? "▾" : "▸"}
          </span>
        </button>
        {terminalOpen && (
          <div className="space-y-2 p-3">
            <RetestTerminal lines={terminalLines} className="h-80" />
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
                  placeholder={sandboxLive ? "Run a command in the sandbox…" : "Sandbox closed"}
                  disabled={!sandboxLive}
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
