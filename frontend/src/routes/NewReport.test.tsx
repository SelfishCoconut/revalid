import { fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Report } from "../api/types";
import { draftsToPayload, type FindingDraft, toLines } from "../lib/manualReport";
import { renderWithProviders } from "../test/utils";
import { NewReport } from "./NewReport";

vi.mock("../api/client");

const readyReport: Report = {
  id: 7,
  filename: "Manual report",
  status: "ready",
  model: "manual",
  error: null,
  finding_count: 1,
  created_at: "2026-07-15T10:00:00Z",
};

function draft(overrides: Partial<FindingDraft> = {}): FindingDraft {
  return { title: "T", severity: "high", description: "", endpoints: "", steps: "", ...overrides };
}

function renderNewReport() {
  return renderWithProviders(
    <Routes>
      <Route path="/new" element={<NewReport />} />
      <Route path="/reports/:id" element={<div>REPORT PAGE</div>} />
    </Routes>,
    "/new",
  );
}

describe("draftsToPayload", () => {
  it("trims fields and splits endpoints into lines", () => {
    const payload = draftsToPayload("  My report  ", [
      draft({ title: " SQLi ", endpoints: "/a\n  /b  \n\n", steps: " 1. do it " }),
    ]);
    expect(payload.label).toBe("My report");
    expect(payload.findings[0]).toEqual({
      title: "SQLi",
      severity: "high",
      description: "",
      endpoints: ["/a", "/b"],
      steps_to_reproduce: "1. do it",
    });
  });

  it("toLines drops blank lines", () => {
    expect(toLines("a\n\n  \nb")).toEqual(["a", "b"]);
  });
});

describe("NewReport", () => {
  beforeEach(() => {
    vi.mocked(client.createManualReport).mockReset();
    vi.mocked(client.createManualReport).mockResolvedValue(readyReport);
  });

  it("submits the form payload and navigates to the new report", async () => {
    renderNewReport();
    fireEvent.change(screen.getByPlaceholderText(/Acme Corp/), {
      target: { value: "My pentest" },
    });
    fireEvent.change(screen.getByPlaceholderText(/^Title/), {
      target: { value: "IDOR basket" },
    });
    fireEvent.change(screen.getByLabelText("Finding 1 severity"), {
      target: { value: "medium" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create report/ }));

    expect(await screen.findByText("REPORT PAGE")).toBeInTheDocument();
    expect(vi.mocked(client.createManualReport).mock.calls[0][0]).toEqual({
      label: "My pentest",
      findings: [
        {
          title: "IDOR basket",
          severity: "medium",
          description: "",
          endpoints: [],
          steps_to_reproduce: "",
        },
      ],
    });
  });

  it("blocks submit and shows an error when the name is empty", () => {
    renderNewReport();
    fireEvent.click(screen.getByRole("button", { name: /Create report/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Give the report a name.");
    expect(client.createManualReport).not.toHaveBeenCalled();
  });

  it("requires every finding to have a title", () => {
    renderNewReport();
    fireEvent.change(screen.getByPlaceholderText(/Acme Corp/), {
      target: { value: "R" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create report/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Every finding needs a title.");
    expect(client.createManualReport).not.toHaveBeenCalled();
  });

  it("adds and removes finding rows", () => {
    renderNewReport();
    expect(screen.getAllByText(/^Finding \d/)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Add finding/ }));
    expect(screen.getAllByText(/^Finding \d/)).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    expect(screen.getAllByText(/^Finding \d/)).toHaveLength(1);
  });

  it("parses and submits pasted JSON", async () => {
    renderNewReport();
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByPlaceholderText(/"label"/), {
      target: {
        value: JSON.stringify({
          label: "From JSON",
          findings: [{ title: "X", severity: "low" }],
        }),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create report/ }));

    expect(await screen.findByText("REPORT PAGE")).toBeInTheDocument();
    expect(vi.mocked(client.createManualReport).mock.calls[0][0]).toEqual({
      label: "From JSON",
      findings: [{ title: "X", severity: "low" }],
    });
  });

  it("loads JSON from an uploaded file into the editor", async () => {
    renderNewReport();
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    const contents = JSON.stringify({ label: "F", findings: [{ title: "Y", severity: "info" }] });
    const file = new File([contents], "findings.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("Findings JSON file"), {
      target: { files: [file] },
    });
    expect(await screen.findByDisplayValue(contents)).toBeInTheDocument();
  });

  it("shows an error for invalid JSON without calling the API", () => {
    renderNewReport();
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByPlaceholderText(/"label"/), {
      target: { value: "{ not valid" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create report/ }));
    expect(screen.getByRole("alert")).toHaveTextContent(/Invalid JSON/);
    expect(client.createManualReport).not.toHaveBeenCalled();
  });
});
