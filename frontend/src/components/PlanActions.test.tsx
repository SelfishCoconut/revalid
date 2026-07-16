import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Plan } from "../api/types";
import type { EditableAction } from "../lib/planActions";
import { PlanActions } from "./PlanActions";

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: 1,
    finding_id: 7,
    version: 1,
    status: "proposed",
    origin: "generated",
    error: null,
    actions: [],
    rejected_actions: [],
    raw: {},
    decided_at: null,
    decided_by: null,
    ...overrides,
  };
}

const action: EditableAction = {
  method: "POST",
  target: "/rest/user/login",
  expected_indicator: "token",
  headers: {},
  json_body: null,
};

describe("PlanActions", () => {
  it("shows applied guidance and dropped actions in read-only mode", () => {
    const plan = makePlan({
      raw: { instructions: "also check /admin" },
      rejected_actions: [{ action: { method: "DELETE", target: "/x", headers: {}, json_body: null, expected_indicator: "" }, reason: "destructive method" }],
    });
    render(<PlanActions plan={plan} actions={[action]} editable={false} />);
    expect(screen.getByText("also check /admin")).toBeInTheDocument();
    expect(screen.getByText(/dropped by the safety gate/i)).toBeInTheDocument();
    expect(screen.getByText(/destructive method/)).toBeInTheDocument();
    // Read-only: no editable input for the target.
    expect(screen.queryByLabelText(/target for action 1/i)).not.toBeInTheDocument();
  });

  it("edits fields and reports changes when editable", async () => {
    const onFieldChange = vi.fn();
    render(
      <PlanActions plan={makePlan()} actions={[action]} editable onFieldChange={onFieldChange} />,
    );
    await userEvent.type(screen.getByLabelText(/target for action 1/i), "!");
    expect(onFieldChange).toHaveBeenCalledWith(0, "target", "/rest/user/login!");
  });

  it("reports an empty plan", () => {
    render(<PlanActions plan={makePlan()} actions={[]} editable={false} />);
    expect(screen.getByText(/no runnable actions/i)).toBeInTheDocument();
  });
});
