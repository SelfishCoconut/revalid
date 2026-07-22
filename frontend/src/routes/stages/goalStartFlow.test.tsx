import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../api/client";
import type { RetestSession } from "../../api/client";
import type { Finding } from "../../api/types";
import { FindingLayout } from "../../components/FindingLayout";
import { renderWithProviders } from "../../test/utils";
import { GoalStage } from "./GoalStage";
import { RetestStage } from "./RetestStage";

// Route-level regression test for the FR-17 6b-iii-b headline bug: starting a
// retest from GoalStage must land on the just-created session's console, not
// bounce back to /goal. That bug is invisible to stage-level tests (see
// stages.test.tsx), which inject `latestSession` directly into the outlet
// context instead of exercising FindingLayout's real cache — so this test
// renders the real route tree (FindingLayout + its real child stages) with a
// real QueryClientProvider, the way the app actually wires them.
vi.mock("../../api/client");
// The console itself (WS machinery, mutations, …) is exercised in
// RetestSession.test.tsx; here we only care that RetestStage found the right
// session id and rendered it, without bouncing to /goal first.
vi.mock("../RetestSession", () => ({
  RetestSession: ({ sessionId }: { sessionId: number }) => (
    <div data-testid="console">session {sessionId}</div>
  ),
}));

const finding: Finding = {
  id: 7,
  report_id: 3,
  version: 1,
  title: "SQLi login",
  severity: "high",
  description: "auth bypass",
  impact: "",
  attack_vector: "",
  affected_endpoints: [],
  reproduction_steps: [],
  cvss: { vector: "", base_score: null, inferred: false },
  mitre: { techniques: [], inferred: false },
  raw: {},
};

const createdSession: RetestSession = {
  id: 5,
  finding_id: 7,
  status: "starting",
  model: "gpt-test",
  verdict_status: null,
  verdict_rationale: null,
  free_launch: false,
  events: [],
};

function renderApp() {
  return renderWithProviders(
    <Routes>
      <Route path="/findings/:id" element={<FindingLayout />}>
        <Route path="goal" element={<GoalStage />} />
        <Route path="retest" element={<RetestStage />} />
      </Route>
    </Routes>,
    "/findings/7/goal",
  );
}

describe("Open-console flow (route-level)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(client.listFindings).mockResolvedValue([finding]);
    vi.mocked(client.listVerdicts).mockResolvedValue([]);
    vi.mocked(client.listRetestSessions).mockResolvedValue([]);
    vi.mocked(client.listNotes).mockResolvedValue([]);
    vi.mocked(client.draftGoal).mockResolvedValue({ steps: ["confirm endpoint", "retry bypass"] });
    vi.mocked(client.startRetestSession).mockResolvedValue(createdSession);
  });

  it("opens the console on the just-created session after Open console, without bouncing to goal", async () => {
    renderApp();

    // Wait for the draft to seed the textarea, confirming GoalStage mounted.
    await screen.findByDisplayValue(/confirm endpoint/);

    await userEvent.click(screen.getByRole("button", { name: /open console/i }));

    // Must land on the console for the *new* session (id 5), not redirect
    // back to /goal because the sessions cache was still the stale `[]`.
    expect(await screen.findByTestId("console")).toHaveTextContent("session 5");
    expect(screen.queryByLabelText("retest goal steps")).not.toBeInTheDocument();
  });
});
