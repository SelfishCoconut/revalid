import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { errorMessage, formatDateTime } from "./format";

describe("formatDateTime", () => {
  it("formats a valid ISO timestamp to a readable local string", () => {
    // Locale/timezone vary, but a midday timestamp always keeps its year.
    expect(formatDateTime("2026-07-14T12:00:00Z")).toContain("2026");
  });

  it("returns the raw string unchanged when it is not a date", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});

describe("errorMessage", () => {
  it("uses the FastAPI detail for an ApiError", () => {
    expect(errorMessage(new ApiError(422, "off-allowlist host"))).toBe("off-allowlist host");
  });

  it("uses the message for a plain Error", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
  });

  it("falls back to a generic message for anything else", () => {
    expect(errorMessage("weird")).toBe("Unexpected error");
    expect(errorMessage(null)).toBe("Unexpected error");
  });
});
