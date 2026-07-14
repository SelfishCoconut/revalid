import { describe, expect, it } from "vitest";

import type { VerdictStatus } from "../api/types";
import { SEVERITY_TONE, STATUS_META, VERDICT_TONE } from "./status";

describe("status tone mapping", () => {
  it("maps each verdict to reality's red/amber/green triad", () => {
    const expected: Record<VerdictStatus, string> = {
      still_open: "danger",
      inconclusive: "warn",
      fixed: "ok",
    };
    expect(VERDICT_TONE).toEqual(expected);
  });

  it("derives verdict tones from the single STATUS_META source", () => {
    expect(VERDICT_TONE.still_open).toBe(STATUS_META.still_open.tone);
    expect(VERDICT_TONE.fixed).toBe(STATUS_META.fixed.tone);
  });

  it("keeps critical severity on the danger tone and info muted", () => {
    expect(SEVERITY_TONE.critical).toBe("danger");
    expect(SEVERITY_TONE.info).toBe("neutral");
  });
});
