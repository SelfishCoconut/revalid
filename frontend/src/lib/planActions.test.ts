import { describe, expect, it } from "vitest";

import type { Probe } from "../api/types";
import { toEditable, toPlannedAction } from "./planActions";

const probe: Probe = {
  kind: "sqli-login-bypass",
  method: "POST",
  url: "http://localhost:3000/rest/user/login",
  headers: { "Content-Type": "application/json" },
  json_body: { email: "a" },
  expected_indicator: "token in body",
};

describe("planActions", () => {
  it("maps a probe to an editable row (url → target)", () => {
    const editable = toEditable(probe);
    expect(editable.target).toBe(probe.url);
    expect(editable.method).toBe("POST");
    expect(editable.expected_indicator).toBe("token in body");
    expect(editable.headers).toEqual(probe.headers);
  });

  it("round-trips an edited row back to a planned action (target → target)", () => {
    const action = toPlannedAction(toEditable(probe));
    expect(action.target).toBe(probe.url);
    expect(action.json_body).toEqual(probe.json_body);
    expect(action.expected_indicator).toBe("token in body");
  });
});
