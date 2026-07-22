import { describe, expect, it } from "vitest";

import { goalStepsToText, parseGoalSteps } from "./goal";

describe("parseGoalSteps", () => {
  it("splits on newlines and trims each step", () => {
    expect(parseGoalSteps("  first \n second\t")).toEqual(["first", "second"]);
  });

  it("drops blank and whitespace-only lines", () => {
    // A trailing newline or a spacer line must not become an empty step.
    expect(parseGoalSteps("first\n\n   \nsecond\n")).toEqual(["first", "second"]);
  });

  it("treats empty text as no steps", () => {
    expect(parseGoalSteps("")).toEqual([]);
    expect(parseGoalSteps("   \n  ")).toEqual([]);
  });
});

describe("goalStepsToText", () => {
  it("renders one step per line", () => {
    expect(goalStepsToText(["a", "b"])).toBe("a\nb");
  });

  it("renders no steps as empty text", () => {
    expect(goalStepsToText([])).toBe("");
  });
});

describe("round trip", () => {
  it("parse(render(steps)) is the identity for clean steps", () => {
    const steps = ["POST /rest/user/login", "check for HTTP 200", "assert a JWT comes back"];
    expect(parseGoalSteps(goalStepsToText(steps))).toEqual(steps);
  });
});
