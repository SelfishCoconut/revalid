import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Plan } from "../api/types";
import { renderWithProviders } from "../test/utils";
import { PlanEditor } from "./PlanEditor";

vi.mock("../api/client");

const proposedPlan: Plan = {
  id: 1,
  finding_id: 42,
  version: 1,
  status: "proposed",
  origin: "llm",
  actions: [
    {
      kind: "sqli_login_bypass",
      method: "POST",
      url: "http://lab.local/rest/user/login",
      headers: { "Content-Type": "application/json" },
      json_body: { email: "' OR 1=1--", password: "x" },
      expected_indicator: "authentication",
    },
  ],
  rejected_actions: [
    {
      action: {
        method: "DELETE",
        target: "http://evil.example/wipe",
        headers: {},
        json_body: null,
        expected_indicator: "gone",
      },
      reason: "off-allowlist host",
    },
  ],
  raw: {},
  decided_at: null,
  decided_by: null,
};

describe("PlanEditor", () => {
  beforeEach(() => {
    vi.mocked(client.approvePlan).mockReset();
    vi.mocked(client.approvePlan).mockResolvedValue({
      ...proposedPlan,
      status: "approved",
    });
  });

  it("renders the plan's actions and its rejected actions", () => {
    renderWithProviders(<PlanEditor findingId={42} plan={proposedPlan} />);

    expect(screen.getByDisplayValue("POST")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("http://lab.local/rest/user/login"),
    ).toBeInTheDocument();
    expect(screen.getByText(/off-allowlist host/)).toBeInTheDocument();
  });

  it("calls approvePlan when Approve is clicked", async () => {
    renderWithProviders(<PlanEditor findingId={42} plan={proposedPlan} />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(client.approvePlan).toHaveBeenCalledWith(42);
    });
  });
});
