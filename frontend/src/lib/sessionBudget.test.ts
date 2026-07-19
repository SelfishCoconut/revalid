import { describe, expect, it } from "vitest";

import type { SessionEvent } from "../api/client";
import {
  autoApprovedSeqs,
  budgetLabel,
  currentFreeLaunch,
  givenUpReason,
  stepsUsed,
} from "./sessionBudget";

const ev = (kind: string, payload: Record<string, unknown> = {}, seq = 0): SessionEvent => ({
  seq,
  kind,
  payload,
});

describe("stepsUsed", () => {
  it("counts command_approved events", () => {
    const events = [ev("command_approved"), ev("command_output"), ev("command_approved")];
    expect(stepsUsed(events)).toBe(2);
  });
  it("is zero when there are none", () => {
    expect(stepsUsed([ev("command_proposed")])).toBe(0);
  });
});

describe("currentFreeLaunch", () => {
  it("follows the latest free_launch_changed event", () => {
    const events = [
      ev("free_launch_changed", { enabled: true }),
      ev("free_launch_changed", { enabled: false }),
    ];
    expect(currentFreeLaunch(events, false)).toBe(false);
  });
  it("falls back to the initial value with no toggle events", () => {
    expect(currentFreeLaunch([ev("command_approved")], true)).toBe(true);
  });
});

describe("budgetLabel", () => {
  it("formats used / max", () => {
    expect(budgetLabel(3, 8)).toBe("3 / 8 steps");
  });

  it("shows no-limit when max is null", () => {
    expect(budgetLabel(3, null)).toBe("3 steps · no limit");
  });
});

describe("givenUpReason", () => {
  it("returns the verdict rationale of a given-up session", () => {
    const events = [ev("verdict", { status: "inconclusive", rationale: "budget exhausted" })];
    expect(givenUpReason(events)).toBe("budget exhausted");
  });
  it("returns null when no verdict is present", () => {
    expect(givenUpReason([ev("command_output")])).toBeNull();
  });
});

describe("autoApprovedSeqs", () => {
  it("flags a proposal whose next decision is an auto approval", () => {
    const events = [
      ev("command_proposed", { command: "id" }, 1),
      ev("command_approved", { auto: true }, 2),
      ev("command_output", { command: "id" }, 3),
    ];
    expect(autoApprovedSeqs(events).has(1)).toBe(true);
  });
  it("does not flag a human-approved or rejected proposal", () => {
    const events = [
      ev("command_proposed", { command: "id" }, 1),
      ev("command_approved", {}, 2), // no auto flag → human approval
      ev("command_proposed", { command: "rm" }, 3),
      ev("command_rejected", { reason: "no" }, 4),
    ];
    const seqs = autoApprovedSeqs(events);
    expect(seqs.has(1)).toBe(false);
    expect(seqs.has(3)).toBe(false);
  });
});
