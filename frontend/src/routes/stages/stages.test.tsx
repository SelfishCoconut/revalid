import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../api/client";
import type { AgenticEvidence, RetestSessionSummary, Verdict } from "../../api/types";
import { renderStage, stageContext } from "../../test/stage";
import { ExtractStage } from "./ExtractStage";
import { GoalStage } from "./GoalStage";
import { RetestStage } from "./RetestStage";
import { StageRedirect } from "./StageRedirect";
import { VerdictStage } from "./VerdictStage";

vi.mock("../../api/client");
// The console itself is exercised in RetestSession.test.tsx (WS machinery,
// mutations, …); here we only need to know RetestStage picked the right
// session and handed it the right id.
vi.mock("../RetestSession", () => ({
  RetestSession: ({ sessionId }: { sessionId: number }) => (
    <div data-testid="retest-session-stub">session {sessionId}</div>
  ),
}));

function verdict(): Verdict {
  const evidence: AgenticEvidence = {
    explanation: "confirmed the login endpoint still returns a valid session token",
    command: "curl -s -X POST http://localhost:3000/rest/user/login",
    output: "HTTP/1.1 200 OK",
    exit_code: 0,
    elapsed_ms: 12,
  };
  return {
    id: 1,
    finding_id: 7,
    status: "still_open",
    reason_code: "auth_bypass",
    rationale: "token returned",
    matched_indicators: [],
    session_id: 42,
    actor: "agent",
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
    renderStage(<VerdictStage />, stageContext({ verdicts: [verdict()] }));
    expect(await screen.findByText(/token returned/i)).toBeInTheDocument();
  });
});

describe("RetestStage", () => {
  it("redirects to goal when the finding has no session", () => {
    renderStage(<RetestStage />, stageContext({ currentStage: "goal", sessions: [] }));
    expect(screen.queryByTestId("retest-session-stub")).not.toBeInTheDocument();
  });

  it("renders the console for the latest session", () => {
    const s: RetestSessionSummary = {
      id: 9,
      finding_id: 7,
      status: "working",
      verdict_status: null,
      created_at: "",
    };
    renderStage(<RetestStage />, stageContext({ sessions: [s], latestSession: s }));
    expect(screen.getByTestId("retest-session-stub")).toHaveTextContent("session 9");
  });
});

describe("GoalStage", () => {
  it("starts a seeded session with the goal and the finding's endpoints as scope", async () => {
    vi.mocked(client.draftGoal).mockResolvedValue({ steps: ["confirm endpoint", "retry bypass"] });
    vi.mocked(client.startRetestSession).mockResolvedValue({ id: 5 } as never);
    renderStage(
      <GoalStage />,
      stageContext({
        currentStage: "goal",
        finding: {
          ...stageContext().finding,
          affected_endpoints: ["http://revalid-juice-shop:3000/rest/user/login"],
        },
      }),
    );
    expect(await screen.findByDisplayValue(/confirm endpoint/)).toBeInTheDocument();
    // The scope editor pre-fills from the finding's endpoints (FR-17 launch scope).
    expect(
      screen.getByDisplayValue("http://revalid-juice-shop:3000/rest/user/login"),
    ).toBeInTheDocument();
    // Opening the console launches **deferred** (#157): the session lands idle,
    // provisioning nothing, and waits for an explicit wake in the console.
    await userEvent.click(screen.getByRole("button", { name: /open console/i }));
    expect(client.startRetestSession).toHaveBeenCalledWith(7, {
      deferred: true,
      initial_goal: ["confirm endpoint", "retry bypass"],
      target_endpoints: ["http://revalid-juice-shop:3000/rest/user/login"],
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

  it("shows the finding's CVSS and ATT&CK taxonomy (FR-19)", () => {
    renderStage(
      <ExtractStage />,
      stageContext({
        finding: {
          ...stageContext().finding,
          cvss: { vector: "CVSS:3.1/AV:N/AC:L", base_score: 9.8, inferred: true },
          mitre: { techniques: ["T1190"], inferred: false },
        },
      }),
    );
    expect(screen.getByText("9.8")).toBeInTheDocument();
    expect(screen.getByText("T1190")).toBeInTheDocument();
    // The CVSS was derived, the ATT&CK mapping was stated — exactly one badge.
    expect(screen.getAllByText("inferred")).toHaveLength(1);
  });

  it("lets the operator score a finding that arrived with no taxonomy (FR-19)", async () => {
    vi.mocked(client.editFinding).mockResolvedValue({ ...stageContext().finding, version: 2 });
    renderStage(<ExtractStage />, stageContext());

    await userEvent.type(
      screen.getByLabelText(/cvss vector/i),
      "CVSS:3.1/AV:N/AC:L/PR:N",
    );
    await userEvent.type(screen.getByLabelText(/cvss base score/i), "9.1");
    await userEvent.type(screen.getByLabelText(/att&ck techniques/i), "T1190, T1110");
    await userEvent.click(screen.getByRole("button", { name: /save as new version/i }));

    await waitFor(() => {
      expect(client.editFinding).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          cvss: { vector: "CVSS:3.1/AV:N/AC:L/PR:N", base_score: 9.1 },
          mitre: { techniques: ["T1190", "T1110"] },
        }),
      );
    });
  });

  it("resubmits an untouched taxonomy unchanged, so the server keeps its provenance", async () => {
    const finding = {
      ...stageContext().finding,
      cvss: { vector: "CVSS:3.1/AV:N/AC:L", base_score: 9.8, inferred: true },
      mitre: { techniques: ["T1190"], inferred: true },
    };
    vi.mocked(client.editFinding).mockResolvedValue({ ...finding, version: 2 });
    renderStage(<ExtractStage />, stageContext({ finding }));

    // Edit something else entirely and save.
    await userEvent.type(screen.getByLabelText(/finding description/i), " corrected");
    await userEvent.click(screen.getByRole("button", { name: /save as new version/i }));

    await waitFor(() => {
      expect(client.editFinding).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          // Sent verbatim and with no `inferred` key — provenance is the
          // server's call, not the form's.
          cvss: { vector: "CVSS:3.1/AV:N/AC:L", base_score: 9.8 },
          mitre: { techniques: ["T1190"] },
        }),
      );
    });
  });

  it("sends a null base score when the field is cleared", async () => {
    const finding = {
      ...stageContext().finding,
      cvss: { vector: "CVSS:3.1/AV:N", base_score: 9.8, inferred: false },
    };
    vi.mocked(client.editFinding).mockResolvedValue({ ...finding, version: 2 });
    renderStage(<ExtractStage />, stageContext({ finding }));

    await userEvent.clear(screen.getByLabelText(/cvss base score/i));
    await userEvent.click(screen.getByRole("button", { name: /save as new version/i }));

    await waitFor(() => {
      expect(client.editFinding).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ cvss: { vector: "CVSS:3.1/AV:N", base_score: null } }),
      );
    });
  });
});

describe("StageRedirect", () => {
  it("renders without crashing (redirects to the current stage)", () => {
    renderStage(<StageRedirect />, stageContext({ currentStage: "goal" }));
    // Navigate renders nothing; the assertion is that render did not throw.
    expect(document.body).toBeTruthy();
  });
});
