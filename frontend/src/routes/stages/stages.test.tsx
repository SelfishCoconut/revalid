import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../api/client";
import type { Evidence, Plan, Verdict } from "../../api/types";
import { renderStage, stageContext } from "../../test/stage";
import { ApproveStage } from "./ApproveStage";
import { ExtractStage } from "./ExtractStage";
import { PlanStage } from "./PlanStage";
import { RetestStage } from "./RetestStage";
import { StageRedirect } from "./StageRedirect";
import { VerdictStage } from "./VerdictStage";

vi.mock("../../api/client");

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: 1,
    finding_id: 7,
    version: 1,
    status: "proposed",
    origin: "generated",
    error: null,
    actions: [
      {
        kind: "sqli-login-bypass",
        method: "POST",
        url: "/rest/user/login",
        headers: {},
        json_body: null,
        expected_indicator: "token",
      },
    ],
    rejected_actions: [],
    raw: {},
    decided_at: null,
    decided_by: null,
    ...overrides,
  };
}

function verdict(): Verdict {
  const evidence: Evidence = {
    request_method: "POST",
    request_url: "http://localhost:3000/rest/user/login",
    request_body: "",
    response_status: 200,
    response_headers: {},
    response_body_excerpt: "",
    elapsed_ms: 12,
  };
  return {
    id: 1,
    finding_id: 7,
    probe_kind: "sqli-login-bypass",
    plan_version: 1,
    status: "still_open",
    reason_code: "auth_bypass",
    rationale: "token returned",
    matched_indicators: [],
    evidence,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(client.listNotes).mockResolvedValue([]);
  vi.mocked(client.listFindingVersions).mockResolvedValue([]);
});

describe("VerdictStage", () => {
  it("prompts to retest when there are no verdicts", async () => {
    renderStage(<VerdictStage />, stageContext());
    expect(await screen.findByText(/no verdicts yet/i)).toBeInTheDocument();
  });

  it("shows the determination and verdict cards once retested", async () => {
    renderStage(<VerdictStage />, stageContext({ verdicts: [verdict()], retested: true }));
    expect(await screen.findByText(/token returned/i)).toBeInTheDocument();
  });
});

describe("RetestStage", () => {
  it("blocks until a plan is approved", async () => {
    renderStage(<RetestStage />, stageContext({ approved: false }));
    expect(await screen.findByText(/approve a plan/i)).toBeInTheDocument();
  });

  it("runs the retest when approved", async () => {
    vi.mocked(client.retest).mockResolvedValue([verdict()]);
    renderStage(<RetestStage />, stageContext({ approved: true }));
    await userEvent.click(screen.getByRole("button", { name: /run retest/i }));
    await waitFor(() => {
      expect(client.retest).toHaveBeenCalledWith(7);
    });
  });

  it("starts an agentic retest session (FR-17)", async () => {
    vi.mocked(client.startRetestSession).mockResolvedValue({
      id: 42,
      finding_id: 7,
      status: "starting",
      model: "claude",
      verdict_status: null,
      verdict_rationale: null,
      events: [],
    });
    renderStage(<RetestStage />, stageContext({ approved: true }));
    await userEvent.click(screen.getByRole("button", { name: /start agentic retest session/i }));
    await waitFor(() => {
      expect(client.startRetestSession).toHaveBeenCalledWith(7);
    });
  });
});

describe("ApproveStage", () => {
  it("prompts to generate a plan when none exists", async () => {
    renderStage(<ApproveStage />, stageContext({ currentPlan: undefined }));
    expect(await screen.findByText(/generate a plan/i)).toBeInTheDocument();
  });

  it("approves a proposed plan", async () => {
    vi.mocked(client.approvePlan).mockResolvedValue(plan({ status: "approved" }));
    renderStage(<ApproveStage />, stageContext({ currentPlan: plan() }));
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    await waitFor(() => {
      expect(client.approvePlan).toHaveBeenCalledWith(7);
    });
  });

  it("un-approves an approved plan", async () => {
    vi.mocked(client.revisePlan).mockResolvedValue(plan({ status: "proposed", version: 2 }));
    renderStage(<ApproveStage />, stageContext({ currentPlan: plan({ status: "approved" }) }));
    await userEvent.click(screen.getByRole("button", { name: /un-approve/i }));
    await waitFor(() => {
      expect(client.revisePlan).toHaveBeenCalledWith(7);
    });
  });
});

describe("PlanStage", () => {
  it("offers generation when there is no plan", async () => {
    renderStage(<PlanStage />, stageContext({ currentPlan: undefined }));
    expect(await screen.findByRole("button", { name: /generate plan/i })).toBeInTheDocument();
  });

  it("shows a spinner while generating", async () => {
    renderStage(<PlanStage />, stageContext({ currentPlan: plan({ status: "generating" }) }));
    expect(await screen.findByText(/generating retest plan/i)).toBeInTheDocument();
  });

  it("saves edited actions on a proposed plan", async () => {
    vi.mocked(client.editPlan).mockResolvedValue(plan({ version: 2 }));
    renderStage(<PlanStage />, stageContext({ currentPlan: plan() }));
    await userEvent.click(screen.getByRole("button", { name: /save edits/i }));
    await waitFor(() => {
      expect(client.editPlan).toHaveBeenCalledWith(7, expect.any(Array));
    });
  });
});

describe("ExtractStage", () => {
  it("saves an edit as a new finding version", async () => {
    vi.mocked(client.editFinding).mockResolvedValue({
      ...stageContext().finding,
      version: 2,
    });
    renderStage(<ExtractStage />, stageContext());
    await userEvent.click(screen.getByRole("button", { name: /save as new version/i }));
    await waitFor(() => {
      expect(client.editFinding).toHaveBeenCalledWith(7, expect.objectContaining({ title: "SQLi login" }));
    });
  });
});

describe("StageRedirect", () => {
  it("renders without crashing (redirects to the current stage)", () => {
    renderStage(<StageRedirect />, stageContext({ currentStage: "plan" }));
    // Navigate renders nothing; the assertion is that render did not throw.
    expect(document.body).toBeTruthy();
  });
});
