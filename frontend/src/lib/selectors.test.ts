import { describe, expect, it } from "vitest";

import type { Plan, Verdict, VerdictStatus } from "../api/types";
import { activePlan, latestVerdict, pipelineReach, verdictCounts } from "./selectors";

function makePlan(overrides: Partial<Plan>): Plan {
  return {
    id: 1,
    finding_id: 1,
    version: 1,
    status: "proposed",
    origin: "llm",
    actions: [],
    rejected_actions: [],
    raw: {},
    decided_at: null,
    decided_by: null,
    ...overrides,
  };
}

function makeVerdict(overrides: Partial<Verdict>): Verdict {
  return {
    id: 1,
    finding_id: 1,
    probe_kind: "probe",
    plan_version: 1,
    status: "still_open",
    reason_code: "CODE",
    rationale: "",
    matched_indicators: [],
    evidence: {
      request_method: "GET",
      request_url: "http://lab.local/",
      request_body: "",
      response_status: 200,
      response_headers: {},
      response_body_excerpt: "",
      elapsed_ms: 1,
    },
    ...overrides,
  };
}

describe("activePlan", () => {
  it("returns undefined when there are no plans", () => {
    expect(activePlan([])).toBeUndefined();
  });

  it("picks the newest version among proposed or approved plans, in any order", () => {
    // Up-then-down (v1 → v3 → v2) exercises both comparator directions.
    const plans = [
      makePlan({ version: 1, status: "approved" }),
      makePlan({ version: 3, status: "proposed" }),
      makePlan({ version: 2, status: "approved" }),
    ];
    expect(activePlan(plans)?.version).toBe(3);
  });

  it("ignores rejected and superseded versions even when they are newer", () => {
    const plans = [
      makePlan({ version: 1, status: "approved" }),
      makePlan({ version: 2, status: "rejected" }),
      makePlan({ version: 3, status: "superseded" }),
    ];
    expect(activePlan(plans)?.version).toBe(1);
  });

  it("returns undefined when every plan is decided", () => {
    const plans = [makePlan({ version: 1, status: "rejected" })];
    expect(activePlan(plans)).toBeUndefined();
  });
});

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

  it("tallies verdicts by status", () => {
    const statuses: VerdictStatus[] = ["still_open", "still_open", "fixed", "inconclusive"];
    const verdicts = statuses.map((status, i) => makeVerdict({ id: i, status }));
    expect(verdictCounts(verdicts)).toEqual({ still_open: 2, inconclusive: 1, fixed: 1 });
  });
});

describe("pipelineReach", () => {
  it("reaches only extract before any action", () => {
    expect(pipelineReach({ planned: false, approved: false, retested: false })).toEqual({
      reached: [true, false, false, false, false],
      current: 0,
    });
  });

  it("advances to plan once a plan exists", () => {
    const { reached, current } = pipelineReach({ planned: true, approved: false, retested: false });
    expect(reached).toEqual([true, true, false, false, false]);
    expect(current).toBe(1);
  });

  it("advances to approve once approved", () => {
    expect(pipelineReach({ planned: true, approved: true, retested: false }).current).toBe(2);
  });

  it("lights the whole track once retested, monotonically", () => {
    // `retested` implies the earlier stages even if their flags were not passed.
    expect(pipelineReach({ planned: false, approved: false, retested: true })).toEqual({
      reached: [true, true, true, true, true],
      current: 4,
    });
  });
});
