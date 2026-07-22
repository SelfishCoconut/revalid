import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Finding, Report, Severity, Verdict } from "../api/types";
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

function makeFinding(id: number, severity: Severity): Finding {
  return {
    id,
    report_id: 1,
    version: 1,
    title: `finding ${String(id)}`,
    severity,
    description: "",
    impact: "",
    attack_vector: "",
    affected_endpoints: [],
    reproduction_steps: [],
    raw: {},
  };
}

function makeVerdict(id: number, findingId: number): Verdict {
  return {
    id,
    finding_id: findingId,
    status: "still_open",
    reason_code: "CODE",
    rationale: "",
    matched_indicators: [],
    session_id: 1,
    actor: "agent",
    evidence: null,
  };
}

/** Read one `label: count` readout out of the meter under the given eyebrow. */
function readout(eyebrow: string | RegExp, label: string): string {
  const section = screen.getByText(eyebrow).parentElement;
  if (!section) throw new Error(`no section under eyebrow ${String(eyebrow)}`);
  // Both meters render bare digits, so scope to the section, then to the
  // labelled readout cell within it.
  const cell = within(section).getByText(label).closest("div");
  if (!cell) throw new Error(`no readout cell for ${label}`);
  return within(cell).getByRole("definition").textContent ?? "";
}

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

  describe("risk profile (#161)", () => {
    beforeEach(() => {
      vi.mocked(client.getReport).mockResolvedValue({
        ...extractingReport,
        status: "ready",
        finding_count: 3,
      });
      vi.mocked(client.listFindings).mockResolvedValue([
        makeFinding(1, "critical"),
        makeFinding(2, "high"),
        makeFinding(3, "high"),
      ]);
      vi.mocked(client.listVerdicts).mockResolvedValue([
        makeVerdict(10, 1),
        makeVerdict(11, 99), // belongs to another report — must not be counted
      ]);
    });

    it("tallies severity over this report's findings", async () => {
      renderWithProviders(
        <Routes>
          <Route path="/reports/:id" element={<ReportDetail />} />
        </Routes>,
        "/reports/1",
      );

      await screen.findByText(/Risk profile/);
      expect(readout(/Risk profile/, "Critical")).toBe("1");
      expect(readout(/Risk profile/, "High")).toBe("2");
      expect(readout(/Risk profile/, "Low")).toBe("0");
    });

    it("keeps the determination ledger scoped to this report's findings", async () => {
      renderWithProviders(
        <Routes>
          <Route path="/reports/:id" element={<ReportDetail />} />
        </Routes>,
        "/reports/1",
      );

      // Two still_open verdicts exist; only finding 1 belongs to this report,
      // so the other report's verdict must not reach the ledger.
      await screen.findByText("Determinations");
      expect(readout("Determinations", "still open")).toBe("1");
    });
  });
});
