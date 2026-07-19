import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Finding, RetestSessionSummary } from "../api/types";
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

const session: RetestSessionSummary = {
  id: 9,
  finding_id: 7,
  status: "concluded",
  verdict_status: "still_open",
  created_at: "",
};

function renderLayout(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/findings/:id" element={<FindingLayout />}>
        <Route index element={<div>child-index</div>} />
        <Route path="extract" element={<div>child-extract</div>} />
        <Route path="goal" element={<div>child-goal</div>} />
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
    vi.mocked(client.listRetestSessions).mockResolvedValue([]);
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
    it("redirects verdict to retest when no session exists yet", async () => {
      // Fresh finding: retest is the current stage, so a deep link straight to
      // verdict is ahead of progress and gets redirected back.
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      renderLayout("/findings/7/verdict");
      expect(await screen.findByText("child-retest")).toBeInTheDocument();
      expect(screen.queryByText("child-verdict")).not.toBeInTheDocument();
    });

    it("makes verdict directly reachable once a session exists", async () => {
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      vi.mocked(client.listRetestSessions).mockResolvedValue([session]);
      renderLayout("/findings/7/verdict");
      expect(await screen.findByText("child-verdict")).toBeInTheDocument();
    });

    it("keeps the current actionable stage (retest) directly reachable", async () => {
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      renderLayout("/findings/7/retest");
      expect(await screen.findByText("child-retest")).toBeInTheDocument();
    });

    it("keeps a reached earlier stage (goal) directly reachable", async () => {
      vi.mocked(client.listFindings).mockResolvedValue([finding]);
      renderLayout("/findings/7/goal");
      expect(await screen.findByText("child-goal")).toBeInTheDocument();
    });
  });
});
