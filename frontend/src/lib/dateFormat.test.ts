import { describe, expect, it } from "vitest";

import { formatDate } from "./dateFormat";

// No timezone suffix → parsed as local, formatted as local, so these are stable
// regardless of where the test runs.
const ISO = "2026-07-20T14:30:00";

describe("formatDate", () => {
  it("formats year-first (the yyyy/mm/dd default)", () => {
    expect(formatDate(ISO, "ymd")).toBe("2026/07/20 14:30");
  });

  it("formats day-first", () => {
    expect(formatDate(ISO, "dmy")).toBe("20/07/2026 14:30");
  });

  it("formats month-first", () => {
    expect(formatDate(ISO, "mdy")).toBe("07/20/2026 14:30");
  });

  it("formats ISO 8601", () => {
    expect(formatDate(ISO, "iso")).toBe("2026-07-20 14:30");
  });

  it("returns the raw string when unparseable", () => {
    expect(formatDate("not-a-date", "ymd")).toBe("not-a-date");
  });
});
