import { describe, expect, it } from "vitest";

import type { Verdict, VerdictStatus } from "../api/types";
import { latestVerdict, pipelineReach, verdictCounts } from "./selectors";

function makeVerdict(overrides: Partial<Verdict>): Verdict {
  return {
    id: 1,
    finding_id: 1,
    status: "still_open",
    reason_code: "CODE",
    rationale: "",
    matched_indicators: [],
    session_id: 1,
    actor: "agent",
    evidence: null,
    ...overrides,
  };
}

describe("latestVerdict", () => {
  it("returns undefined when the finding has no verdicts", () => {
    expect(latestVerdict([], 42)).toBeUndefined();
  });

  it("returns the highest-id verdict for the given finding, ignoring others", () => {
    // Up-then-down among finding 42 (1 → 9 → 5) exercises both comparator branches.
    const verdicts = [
      makeVerdict({ id: 1, finding_id: 42 }),
      makeVerdict({ id: 9, finding_id: 42 }),
      makeVerdict({ id: 5, finding_id: 42 }),
      makeVerdict({ id: 12, finding_id: 7 }),
    ];
    expect(latestVerdict(verdicts, 42)?.id).toBe(9);
  });
});

describe("verdictCounts", () => {
  it("returns all-zero counts for no verdicts", () => {
    expect(verdictCounts([])).toEqual({ still_open: 0, inconclusive: 0, fixed: 0 });
  });

  it("tallies one determination per finding (distinct findings)", () => {
    const statuses: VerdictStatus[] = ["still_open", "still_open", "fixed", "inconclusive"];
    const verdicts = statuses.map((status, i) => makeVerdict({ id: i, finding_id: i, status }));
    expect(verdictCounts(verdicts)).toEqual({ still_open: 2, inconclusive: 1, fixed: 1 });
  });

  it("counts only the latest verdict per finding (supersedes re-runs / adjudications)", () => {
    // Finding 1's verdicts arrive out of order (1 → 3 → 2); only its latest by id
    // (id=3, fixed) counts — the id=2 seen after id=3 exercises the not-latest
    // (`id > current.id` is false) branch. Finding 2 contributes one (still_open).
    const verdicts = [
      makeVerdict({ id: 1, finding_id: 1, status: "still_open" }),
      makeVerdict({ id: 3, finding_id: 1, status: "fixed" }),
      makeVerdict({ id: 2, finding_id: 1, status: "inconclusive" }),
      makeVerdict({ id: 4, finding_id: 2, status: "still_open" }),
    ];
    expect(verdictCounts(verdicts)).toEqual({ still_open: 1, inconclusive: 0, fixed: 1 });
  });

  it("keeps the highest-id verdict when rows arrive out of order", () => {
    // The newer determination (id=5, fixed) is seen before the older one
    // (id=2, still_open); the older must be ignored — exercises the comparator's
    // false branch (`verdict.id > current.id` is false).
    const verdicts = [
      makeVerdict({ id: 5, finding_id: 1, status: "fixed" }),
      makeVerdict({ id: 2, finding_id: 1, status: "still_open" }),
    ];
    expect(verdictCounts(verdicts)).toEqual({ still_open: 0, inconclusive: 0, fixed: 1 });
  });
});

describe("pipelineReach (4-stage)", () => {
  it("extract+goal reachable, retest/verdict not, before any session", () => {
    const r = pipelineReach({ sessionExists: false, hasVerdict: false });
    expect(r.reached).toEqual([true, true, false, false]);
    expect(r.current).toBe(2); // retest is the next action
  });
  it("retest reached once a session exists", () => {
    const r = pipelineReach({ sessionExists: true, hasVerdict: false });
    expect(r.reached).toEqual([true, true, true, false]);
    expect(r.current).toBe(3);
  });
  it("verdict reached once a verdict exists", () => {
    const r = pipelineReach({ sessionExists: true, hasVerdict: true });
    expect(r.reached).toEqual([true, true, true, true]);
    expect(r.furthest).toBe(3);
  });
});
