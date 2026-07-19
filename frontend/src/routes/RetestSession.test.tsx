import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import * as hook from "../hooks/useRetestSession";
import { renderWithProviders } from "../test/utils";
import { RetestSession } from "./RetestSession";

vi.mock("../hooks/useRetestSession");
vi.mock("../api/client");

// The console is a self-contained component (Task 9): it takes `sessionId` as
// a prop rather than reading it from the URL, so no route/MemoryRouter path
// match is needed here — `renderWithProviders` still supplies the router
// context other providers (e.g. links elsewhere in the tree) may expect.
function renderAt(id = 1) {
  return renderWithProviders(<RetestSession sessionId={id} />);
}

/** A default session record for the config `useQuery` (FR-17 Slice 5). */
function mockRecord(overrides: Partial<client.RetestSession> = {}): void {
  vi.mocked(client.getRetestSession).mockResolvedValue({
    id: 1,
    finding_id: 1,
    status: "awaiting_command",
    model: "test",
    verdict_status: null,
    verdict_rationale: null,
    free_launch: false,
    max_steps: 8,
    max_seconds: null,
    events: [],
    ...overrides,
  });
}

describe("RetestSession", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockRecord();
  });

  it("shows the approval card and approves the proposed command", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "curl -s http://lab/rest/user/login", rationale: "retry", tool_call_id: "abc" },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.approveCommand).mockResolvedValue({ status: "approved" });

    renderAt(1);

    expect(screen.getByText(/retry/)).toBeInTheDocument();
    expect(screen.getByText(/curl -s http:\/\/lab\/rest\/user\/login/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(client.approveCommand).toHaveBeenCalledWith(1, "abc");
  });

  it("rejects the proposed command", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "rm -rf /", rationale: "cleanup", tool_call_id: "xyz" },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.rejectCommand).mockResolvedValue({ status: "rejected" });

    renderAt(1);

    await userEvent.click(screen.getByRole("button", { name: /reject/i }));
    expect(client.rejectCommand).toHaveBeenCalledWith(1, "xyz");
  });

  it("disables Approve while pending and surfaces an error when approveCommand rejects", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "id", rationale: "check user", tool_call_id: "abc" },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    let rejectApproval!: (reason: unknown) => void;
    vi.mocked(client.approveCommand).mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectApproval = reject;
      }),
    );

    renderAt(1);

    const approveButton = screen.getByRole("button", { name: /approve/i });
    await userEvent.click(approveButton);

    // Pending: the button that triggered the mutation is disabled so a
    // second click can't fire a duplicate approval while the first is
    // in flight.
    expect(approveButton).toBeDisabled();
    expect(client.approveCommand).toHaveBeenCalledTimes(1);

    rejectApproval(new Error("gateway timeout"));

    expect(await screen.findByRole("alert")).toHaveTextContent("gateway timeout");
    await waitFor(() => {
      expect(approveButton).not.toBeDisabled();
    });
  });

  it("does not show the approval card once the command is no longer awaited", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "id", rationale: "check user", tool_call_id: "abc" },
        },
      ],
      status: "running_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("renders the verdict banner", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 2, kind: "verdict", payload: { status: "still_open", rationale: "bypassable" } }],
      status: "concluded",
      verdict: { status: "still_open", rationale: "bypassable" },
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText(/bypassable/)).toBeInTheDocument();
    expect(screen.getByText(/still open/i)).toBeInTheDocument();
  });

  function mockConcluded(): void {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 2, kind: "verdict", payload: { status: "still_open", rationale: "bypassable" } }],
      status: "concluded",
      verdict: { status: "still_open", rationale: "bypassable" },
      connected: true,
    });
  }

  it("adjudication panel is absent while the session is live", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    renderAt(1);
    expect(screen.queryByText(/adjudication/i)).not.toBeInTheDocument();
  });

  it("Accept records the agent's own verdict (FR-17 Slice 6a)", async () => {
    mockConcluded();
    vi.mocked(client.adjudicateSession).mockResolvedValue({ status: "adjudicated" });

    renderAt(1);
    expect(screen.getByText(/adjudication/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^accept$/i }));
    expect(client.adjudicateSession).toHaveBeenCalledWith(1, "still_open", "bypassable");
  });

  it("Override submits a different verdict and rationale (FR-17 Slice 6a)", async () => {
    mockConcluded();
    vi.mocked(client.adjudicateSession).mockResolvedValue({ status: "adjudicated" });

    renderAt(1);
    await userEvent.click(screen.getByRole("button", { name: /override/i }));
    await userEvent.selectOptions(screen.getByLabelText(/override status/i), "inconclusive");
    await userEvent.type(screen.getByLabelText(/override rationale/i), "need more evidence");
    await userEvent.click(screen.getByRole("button", { name: /submit override/i }));

    expect(client.adjudicateSession).toHaveBeenCalledWith(1, "inconclusive", "need more evidence");
  });

  it("ends the session", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "starting",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.endRetestSession).mockResolvedValue({ status: "ended" });

    renderAt(1);

    await userEvent.click(screen.getByRole("button", { name: /end session/i }));
    expect(client.endRetestSession).toHaveBeenCalledWith(1);
  });

  it("surfaces an error when endRetestSession fails", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "starting",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.endRetestSession).mockRejectedValue(new Error("boom"));

    renderAt(1);

    const endButton = screen.getByRole("button", { name: /end session/i });
    await userEvent.click(endButton);

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("renders an agent_message as a chat turn", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "agent_message", payload: { text: "checking the login flow first" } }],
      status: "thinking",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText(/checking the login flow first/)).toBeInTheDocument();
  });

  it("renders a rejected-command marker with its reason", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "rm -rf /", rationale: "cleanup", tool_call_id: "xyz" },
        },
        { seq: 2, kind: "command_rejected", payload: { reason: "too destructive" } },
      ],
      status: "thinking",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText(/command rejected: too destructive/)).toBeInTheDocument();
    // A rejected command never ran, so it does not carry an approve control.
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("counts executed-command output in the terminal header and collapses it", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_output",
          payload: { command: "curl -s http://lab/rest", stdout: '{"ok":true}', stderr: "", exit_code: 0 },
        },
      ],
      status: "thinking",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    // `$ curl …` + one stdout line = 2 terminal lines; the docked terminal body renders.
    expect(screen.getByText(/2 lines/)).toBeInTheDocument();
    expect(screen.getByTestId("retest-terminal")).toBeInTheDocument();

    // The header doubles as the collapse toggle: hides the terminal body, keeps the count.
    await userEvent.click(screen.getByRole("button", { name: /terminal/i }));
    expect(screen.queryByTestId("retest-terminal")).not.toBeInTheDocument();
    expect(screen.getByText(/2 lines/)).toBeInTheDocument();
  });

  it("runs a command typed into the terminal prompt (agent observes it)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.submitHumanCommand).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    // No `!` prefix any more — commands go straight into the terminal's own prompt.
    await userEvent.type(screen.getByLabelText(/terminal command input/i), "whoami");
    await userEvent.click(screen.getByRole("button", { name: /run/i }));

    expect(client.submitHumanCommand).toHaveBeenCalledWith(1, "whoami");
    expect(client.submitMessage).not.toHaveBeenCalled();
  });

  it("sends composer text to the agent as a chat message", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.submitMessage).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    await userEvent.type(screen.getByLabelText(/message the agent/i), "focus on login");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(client.submitMessage).toHaveBeenCalledWith(1, "focus on login");
    expect(client.submitHumanCommand).not.toHaveBeenCalled();
  });

  it("restarts: ends this session and opens a fresh one seeded with the goal", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "plan_updated", payload: { steps: ["Check /admin"] } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.endRetestSession).mockResolvedValue({ status: "ended" });
    vi.mocked(client.startRetestSession).mockResolvedValue({
      id: 2,
      finding_id: 1,
      status: "starting",
      model: "test",
      verdict_status: null,
      verdict_rationale: null,
      free_launch: false,
      max_steps: 8,
      max_seconds: null,
      events: [],
    });

    renderAt(1);

    // Restart enables once the session record (carrying its finding id) has loaded.
    const restart = await screen.findByRole("button", { name: /restart/i });
    await waitFor(() => {
      expect(restart).not.toBeDisabled();
    });
    await userEvent.click(restart);

    expect(client.endRetestSession).toHaveBeenCalledWith(1);
    await waitFor(() => {
      expect(client.startRetestSession).toHaveBeenCalledWith(1, { initial_goal: ["Check /admin"] });
    });
  });

  it("renders a human_message as an operator turn", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "human_message", payload: { text: "focus on login" } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText("focus on login")).toBeInTheDocument();
  });

  it("disables the message composer once the session is over", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "concluded",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByLabelText(/message the agent/i)).toBeDisabled();
  });

  it("shows operator commands in the docked terminal, marked apart from the agent's", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "human_command",
          payload: { command: "whoami", stdout: "you", stderr: "", exit_code: 0 },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    // `operator$ whoami` + one stdout line = 2 terminal lines.
    expect(screen.getByText(/2 lines/)).toBeInTheDocument();
  });

  it("disables the terminal command prompt once the session is over", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "concluded",
      verdict: { status: "still_open", rationale: "bypassable" },
      connected: true,
    });

    renderAt(1);

    expect(screen.getByLabelText(/terminal command input/i)).toBeDisabled();
  });

  it("shows the current guiding plan in the plan panel", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        { seq: 1, kind: "plan_updated", payload: { steps: ["Retry the payload", "Baseline creds"] } },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText("Retry the payload")).toBeInTheDocument();
    expect(screen.getByText("Baseline creds")).toBeInTheDocument();
    expect(screen.getByText(/2 steps/)).toBeInTheDocument();
  });

  it("shows a placeholder in the goal panel before any goal exists", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "starting",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText(/no goal set yet/i)).toBeInTheDocument();
  });

  function mockLiveGoal(steps: string[]): void {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "plan_updated", payload: { steps } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
  }

  it("edits the current goal (FR-17 6b-ii)", async () => {
    mockLiveGoal(["Old step"]);
    vi.mocked(client.setSessionGoal).mockResolvedValue({ status: "accepted" });

    renderAt(1);
    await userEvent.click(screen.getByRole("button", { name: /edit goal/i }));
    const box = screen.getByLabelText(/goal steps/i);
    await userEvent.clear(box);
    await userEvent.type(box, "Check /admin\nConfirm 200");
    await userEvent.click(screen.getByRole("button", { name: /save goal/i }));

    expect(client.setSessionGoal).toHaveBeenCalledWith(1, ["Check /admin", "Confirm 200"]);
  });

  it("regenerates the goal (FR-17 6b-ii)", async () => {
    mockLiveGoal(["Old step"]);
    vi.mocked(client.regenerateSessionGoal).mockResolvedValue({ status: "accepted" });

    renderAt(1);
    await userEvent.click(screen.getByRole("button", { name: /regenerate goal/i }));

    expect(client.regenerateSessionGoal).toHaveBeenCalledWith(1);
  });

  it("hides goal edit controls once the session is over", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "plan_updated", payload: { steps: ["done"] } }],
      status: "concluded",
      verdict: { status: "fixed", rationale: "patched" },
      connected: true,
    });

    renderAt(1);

    expect(screen.queryByRole("button", { name: /edit goal/i })).not.toBeInTheDocument();
  });

  it("shows the step-budget meter (steps used / max)", async () => {
    mockRecord({ max_steps: 5 });
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        { seq: 1, kind: "command_approved", payload: { auto: true } },
        { seq: 2, kind: "command_output", payload: { command: "id", stdout: "ok" } },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    // The meter first renders with the default budget, then updates to 5 once
    // the config query resolves — wait for the updated readout.
    expect(await screen.findByText("1 / 5 steps")).toBeInTheDocument();
  });

  it("toggles free-launch via the endpoint", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.setFreeLaunch).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    await userEvent.click(screen.getByRole("checkbox", { name: /free-launch/i }));
    expect(client.setFreeLaunch).toHaveBeenCalledWith(1, true);
  });

  it("tags an auto-approved command and shows no approval card for it", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        { seq: 1, kind: "command_proposed", payload: { command: "id", rationale: "who am i" } },
        { seq: 2, kind: "command_approved", payload: { auto: true } },
        { seq: 3, kind: "command_output", payload: { command: "id", stdout: "root" } },
      ],
      status: "concluded",
      verdict: { status: "still_open", rationale: "bypassable" },
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText("auto")).toBeInTheDocument();
    // Auto-run commands never showed an approve/reject card.
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("renders a distinct given-up banner citing the budget reason", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "verdict", payload: { status: "inconclusive", rationale: "budget exhausted" } }],
      status: "given_up",
      verdict: { status: "inconclusive", rationale: "budget exhausted" },
      connected: true,
    });

    renderAt(1);

    expect(screen.getByText(/agent gave up/i)).toBeInTheDocument();
    expect(screen.getByText("budget exhausted")).toBeInTheDocument();
    // Not rendered as an ordinary "Verdict" box.
    expect(screen.queryByText("Verdict")).not.toBeInTheDocument();
  });

  it("lays out the goal panel and the chat log side by side, not dropping either", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        { seq: 1, kind: "plan_updated", payload: { steps: ["Retry the payload"] } },
        { seq: 2, kind: "agent_message", payload: { text: "checking the login flow first" } },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    // Both the goal (now a right column, FR-17 6b-iii-b) and the chat log
    // (the "Agent conversation" region) are present in the same view — the
    // relayout is presentational, it never trades one for the other.
    expect(screen.getByText("Current goal")).toBeInTheDocument();
    expect(screen.getByRole("log", { name: /agent conversation/i })).toBeInTheDocument();
    expect(screen.getByText("Retry the payload")).toBeInTheDocument();
    expect(screen.getByText(/checking the login flow first/)).toBeInTheDocument();
  });
});
