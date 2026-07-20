import userEvent from "@testing-library/user-event";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Report } from "../api/types";
import { renderWithProviders } from "../test/utils";
import { UploadReport } from "./UploadReport";

vi.mock("../api/client");

const extractingReport: Report = {
  id: 7,
  filename: "report.pdf",
  status: "extracting",
  model: "claude",
  error: null,
  finding_count: 0,
  archived: false,
  created_at: "2026-07-14T10:00:00Z",
};

describe("UploadReport", () => {
  beforeEach(() => {
    vi.mocked(client.uploadReport).mockReset();
  });

  it("uploads the chosen PDF and surfaces the new extracting report", async () => {
    vi.mocked(client.uploadReport).mockResolvedValue(extractingReport);
    renderWithProviders(<UploadReport />);

    const file = new File(["%PDF-1.4 test"], "report.pdf", {
      type: "application/pdf",
    });
    const input = screen.getByLabelText("Report PDF");
    await userEvent.upload(input, file);

    expect(client.uploadReport).toHaveBeenCalledTimes(1);
    const uploaded = vi.mocked(client.uploadReport).mock.calls[0][0];
    expect(uploaded).toBeInstanceOf(File);
    expect(uploaded.name).toBe("report.pdf");

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    expect(await screen.findByText("extracting")).toBeInTheDocument();
  });
});
