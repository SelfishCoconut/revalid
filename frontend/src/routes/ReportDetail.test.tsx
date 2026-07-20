import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Report } from "../api/types";
import { renderWithProviders } from "../test/utils";
import { ReportDetail } from "./ReportDetail";

vi.mock("../api/client");

const extractingReport: Report = {
  id: 1,
  filename: "juice-shop.pdf",
  status: "extracting",
  model: "claude",
  error: null,
  finding_count: 0,
  archived: false,
  content_hash: null,
  metadata: null,
  created_at: "2026-07-14T10:00:00Z",
};

describe("ReportDetail", () => {
  beforeEach(() => {
    vi.mocked(client.getReport).mockReset();
    vi.mocked(client.getReport).mockResolvedValue(extractingReport);
  });

  it("shows a spinner while the report is extracting", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/reports/:id" element={<ReportDetail />} />
      </Routes>,
      "/reports/1",
    );

    expect(
      await screen.findByRole("status", { name: "Extracting findings" }),
    ).toBeInTheDocument();
    expect(client.listFindings).not.toHaveBeenCalled();
  });
});
