import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Finding, Plan } from "../api/types";
import { renderWithProviders } from "../test/utils";
import { FindingLayout } from "./FindingLayout";

vi.mock("../api/client");

const finding: Finding = {
  id: 7,
  report_id: 3,
  version: 2,
  title: "SQLi login",
  severity: "critical",
  description: "auth bypass",
  impact: "",
  attack_vector: "",
  affected_endpoints: [],
  reproduction_steps: [],
  raw: {},
};

const approvedPlan: Plan = {
  id: 1,
  finding_id: 7,
  version: 1,
  status: "approved",
  origin: "generated",
  error: null,
  actions: [],
  rejected_actions: [],
  raw: {},
  decided_at: null,
  decided_by: null,
};

function renderLayout(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/findings/:id" element={<FindingLayout />}>
        <Route index element={<div>child-index</div>} />
        <Route path="extract" element={<div>child-extract</div>} />
        <Route path="plan" element={<div>child-plan</div>} />
        <Route path="approve" element={<div>child-approve</div>} />
        <Route path="retest" element={<div>child-retest</div>} />
        <Route path="verdict" element={<div>child-verdict</div>} />
      </Route>
    </Routes>,
    route,
  );
}

describe("FindingLayout", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(client.listPlans).mockResolvedValue([]);
    vi.mocked(client.listVerdicts).mockResolvedValue([]);
  });

  it("pins the finding identity + stepper and renders the active stage", async () => {
    vi.mocked(client.listFindings).mockResolvedValue([finding]);
    renderLayout("/findings/7/extract");

    expect(await screen.findByRole("heading", { name: "SQLi login" })).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    // The stepper links extract (always reached) to its page.
    expect(screen.getByRole("link", { name: /go to extract stage/i })).toBeInTheDocument();
    // The active stage panel renders in the Outlet.
    expect(screen.getByText("child-extract")).toBeInTheDocument();
  });

  it("reports a missing finding", async () => {
    vi.mocked(client.listFindings).mockResolvedValue([]);
    renderLayout("/findings/7/extract");
    expect(await screen.findByText(/finding not found/i)).toBeInTheDocument();
  });

  describe("deep-link redirect", () => {
    it("redirects a stage ahead of progress to the current stage", async () => {
      // Fresh finding: only `extract` is reached, so `plan` is the current stage.
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      renderLayout("/findings/7/approve");
      expect(await screen.findByText("child-plan")).toBeInTheDocument();
      expect(screen.queryByText("child-approve")).not.toBeInTheDocument();
    });

    it("redirects verdict to retest when approved but not retested", async () => {
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      vi.mocked(client.listPlans).mockResolvedValue([approvedPlan]);
      renderLayout("/findings/7/verdict");
      expect(await screen.findByText("child-retest")).toBeInTheDocument();
      expect(screen.queryByText("child-verdict")).not.toBeInTheDocument();
    });

    it("keeps the current actionable stage directly reachable", async () => {
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      renderLayout("/findings/7/plan");
      expect(await screen.findByText("child-plan")).toBeInTheDocument();
    });

    it("keeps a reached earlier stage directly reachable", async () => {
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      renderLayout("/findings/7/extract");
      expect(await screen.findByText("child-extract")).toBeInTheDocument();
    });
  });
});
