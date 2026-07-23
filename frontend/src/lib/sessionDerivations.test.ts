import { describe, expect, it } from "vitest";

import type { SessionEvent } from "../api/client";
import {
  autoApprovedSeqs,
  currentFreeLaunch,
  errorReason,
  givenUpReason,
} from "./sessionDerivations";

const ev = (kind: string, payload: Record<string, unknown> = {}, seq = 0): SessionEvent => ({
  seq,
  kind,
  payload,
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

describe("givenUpReason", () => {
  it("returns the verdict rationale of a given-up session", () => {
    const events = [ev("verdict", { status: "inconclusive", rationale: "no exploit path found" })];
    expect(givenUpReason(events)).toBe("no exploit path found");
  });
  it("returns null when no verdict is present", () => {
    expect(givenUpReason([ev("command_output")])).toBeNull();
  });
});

describe("errorReason", () => {
  it("returns the detail of the latest error event", () => {
    const events = [
      ev("error", { detail: "the sandbox extra is required" }, 1),
      ev("state_change", { to: "error" }, 2),
    ];
    expect(errorReason(events)).toBe("the sandbox extra is required");
  });
  it("returns null when no error event is present", () => {
    expect(errorReason([ev("command_output")])).toBeNull();
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
