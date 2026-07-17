import type { SessionEvent } from "../api/client";

/**
 * Pure derivations for the FR-17 Slice 5 budget/free-launch UI. The transcript
 * (the WS event stream) is the source of truth: steps-used and the live
 * free-launch state are both derived from events so they survive a reload.
 */

/** Steps used so far = the number of approved commands (human or auto). */
export function stepsUsed(events: SessionEvent[]): number {
  return events.filter((e) => e.kind === "command_approved").length;
}

/**
 * The session's current free-launch state: the latest `free_launch_changed`
 * event's `enabled`, or the session's initial value if it was never toggled.
 */
export function currentFreeLaunch(events: SessionEvent[], initial: boolean): boolean {
  const latest = [...events].reverse().find((e) => e.kind === "free_launch_changed");
  return latest ? Boolean(latest.payload.enabled) : initial;
}

/** "3 / 8 steps" — the step-budget meter label. */
export function budgetLabel(used: number, max: number): string {
  return `${String(used)} / ${String(max)} steps`;
}

/** The rationale of a given-up session's verdict, or null if none is recorded. */
export function givenUpReason(events: SessionEvent[]): string | null {
  const verdict = [...events].reverse().find((e) => e.kind === "verdict");
  if (!verdict) return null;
  return String(verdict.payload.rationale ?? "") || null;
}

/**
 * Seqs of `command_proposed` events that were auto-approved under free-launch —
 * a proposal whose next command decision in the (strictly ordered) transcript is
 * a `command_approved` flagged `auto`. Used to tag auto-run commands in the chat
 * (they never showed an approve/reject card).
 */
export function autoApprovedSeqs(events: SessionEvent[]): Set<number> {
  const result = new Set<number>();
  events.forEach((event, i) => {
    if (event.kind !== "command_proposed") return;
    const decision = events
      .slice(i + 1)
      .find((e) => e.kind === "command_approved" || e.kind === "command_rejected");
    if (decision?.kind === "command_approved" && decision.payload.auto === true) {
      result.add(event.seq);
    }
  });
  return result;
}
