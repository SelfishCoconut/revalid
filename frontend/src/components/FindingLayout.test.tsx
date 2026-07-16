import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Finding } from "../api/types";
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

function renderLayout(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/findings/:id" element={<FindingLayout />}>
        <Route path="extract" element={<div>child-extract</div>} />
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
});
