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
      thinking: "",
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
      thinking: "",
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
      thinking: "",
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
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
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
      thinking: "",
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
      thinking: "",
    });
  }

  it("adjudication panel is absent while the session is live", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
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
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
    });
    vi.mocked(client.endRetestSession).mockResolvedValue({ status: "ended" });

    renderAt(1);

    await userEvent.click(screen.getByRole("button", { name: /end session/i }));
    expect(client.endRetestSession).toHaveBeenCalledWith(1);
  });

  it("surfaces an error when endRetestSession fails", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
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
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
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
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
    });

    renderAt(1);

    expect(screen.getByText(/command declined: too destructive/i)).toBeInTheDocument();
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
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
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
      thinking: "",
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
      thinking: "",
    });
    vi.mocked(client.submitMessage).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    await userEvent.type(screen.getByLabelText(/message the agent/i), "focus on login");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(client.submitMessage).toHaveBeenCalledWith(1, "focus on login");
    expect(client.submitHumanCommand).not.toHaveBeenCalled();
  });

  it("restarts: ends this session and opens a fresh deferred one seeded with the goal", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "plan_updated", payload: { steps: ["Check /admin"] } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });
    vi.mocked(client.endRetestSession).mockResolvedValue({ status: "ended" });
    vi.mocked(client.startRetestSession).mockResolvedValue({
      id: 2,
      finding_id: 1,
      status: "working",
      model: "test",
      verdict_status: null,
      verdict_rationale: null,
      free_launch: false,
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
    // Restart now opens the fresh session `deferred` (issue #150): it lands idle and
    // waits for Start rather than auto-running.
    await waitFor(() => {
      expect(client.startRetestSession).toHaveBeenCalledWith(1, {
        deferred: true,
        initial_goal: ["Check /admin"],
      });
    });
  });

  it("renders a human_message as an operator turn", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "human_message", payload: { text: "focus on login" } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
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
      thinking: "",
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
      thinking: "",
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
      thinking: "",
    });

    renderAt(1);

    expect(screen.getByLabelText(/terminal command input/i)).toBeDisabled();
  });

  it("shows the current guiding goal in the goal panel", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        { seq: 1, kind: "plan_updated", payload: { steps: ["Retry the payload", "Baseline creds"] } },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });

    renderAt(1);

    expect(screen.getByText("Retry the payload")).toBeInTheDocument();
    expect(screen.getByText("Baseline creds")).toBeInTheDocument();
    expect(screen.getByText(/current goal/i)).toBeInTheDocument();
  });

  it("shows a placeholder in the goal panel before any goal exists", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
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
      thinking: "",
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
      thinking: "",
    });

    renderAt(1);

    expect(screen.queryByRole("button", { name: /edit goal/i })).not.toBeInTheDocument();
  });

  it("shows a live thinking indicator while the agent computes a turn", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "working",
      verdict: null,
      connected: true,
      thinking: "",
    });

    renderAt(1);

    expect(screen.getByLabelText(/agent thinking/i)).toBeInTheDocument();
  });

  it("toggles auto-run via the endpoint", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });
    vi.mocked(client.setFreeLaunch).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    await userEvent.click(screen.getByRole("checkbox", { name: /auto-run/i }));
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
      thinking: "",
    });

    renderAt(1);

    expect(screen.getByText(/ran automatically/i)).toBeInTheDocument();
    // Auto-run commands never showed an approve/reject card.
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("renders a distinct ended banner citing the agent's reason", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "verdict",
          payload: { status: "inconclusive", rationale: "no exploit path found" },
        },
      ],
      status: "given_up",
      verdict: { status: "inconclusive", rationale: "no exploit path found" },
      connected: true,
      thinking: "",
    });

    renderAt(1);

    expect(screen.getByText(/retest ended/i)).toBeInTheDocument();
    expect(screen.getByText("no exploit path found")).toBeInTheDocument();
    // Not rendered as an ordinary "Verdict" box.
    expect(screen.queryByText("Verdict")).not.toBeInTheDocument();
  });

  function mockPaused(reason = "ran that — I'd try /rest next"): void {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      // The hand-back (guided report, recommendation, or "I'm stuck") is an ordinary
      // agent message now, in awaiting_operator — one agent, one voice (ADR-0042).
      events: [{ seq: 1, kind: "agent_message", payload: { text: reason } }],
      status: "awaiting_operator",
      verdict: null,
      connected: true,
      thinking: "",
    });
  }

  it("surfaces the hand-back message in the chat and prompts a verdict (ADR-0042)", () => {
    mockPaused("exhausted my options, need guidance");
    renderAt(1);
    // No heavy old "needs your guidance" banner — the agent's message carries it,
    expect(screen.queryByLabelText(/needs guidance/i)).not.toBeInTheDocument();
    expect(screen.getByText(/exhausted my options/)).toBeInTheDocument();
    // but the operator is prompted (your move) to steer on or record the verdict.
    expect(screen.getByLabelText(/handed back/i)).toBeInTheDocument();
  });

  it("offers no Keep going button — replying is what resumes it (#163)", async () => {
    mockPaused();
    vi.mocked(client.submitMessage).mockResolvedValue({ status: "accepted" });
    renderAt(1);

    expect(screen.queryByRole("button", { name: /keep going/i })).not.toBeInTheDocument();
    // The composer is the resume path: the server continues the session on receipt.
    await userEvent.type(screen.getByLabelText(/message the agent/i), "try the basket endpoint");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(client.submitMessage).toHaveBeenCalledWith(1, "try the basket endpoint");
  });

  it("Conclude records the operator's own verdict", async () => {
    mockPaused();
    vi.mocked(client.concludeSession).mockResolvedValue({ status: "accepted" });
    renderAt(1);
    await userEvent.click(screen.getByRole("button", { name: /conclude/i }));
    await userEvent.selectOptions(screen.getByLabelText(/conclude status/i), "fixed");
    await userEvent.type(screen.getByLabelText(/conclude rationale/i), "patched by hand");
    await userEvent.click(screen.getByRole("button", { name: /record verdict/i }));
    expect(client.concludeSession).toHaveBeenCalledWith(1, "fixed", "patched by hand");
  });

  it("keeps the composer and terminal usable while the agent has handed back", () => {
    mockPaused();
    renderAt(1);
    // awaiting_operator is non-terminal: the operator steers by chatting and running
    // commands, so neither input is disabled.
    expect(screen.getByLabelText(/message the agent/i)).not.toBeDisabled();
    expect(screen.getByLabelText(/terminal command input/i)).not.toBeDisabled();
  });

  it("wakes an idle (deferred) session by messaging it (#163)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "plan_updated", payload: { steps: ["Re-check login"] } }],
      status: "idle",
      verdict: null,
      connected: true,
      thinking: "",
    });
    mockRecord({ status: "idle" });
    vi.mocked(client.submitMessage).mockResolvedValue({ status: "accepted" });
    renderAt(1);

    // Idle: nothing has run — the goal shows and the thread says the agent is
    // asleep. There is no wake *button* (#163) and no Stop; the composer is live
    // even though there is no sandbox yet, because messaging is what provisions it.
    expect(screen.getByLabelText(/asleep/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /wake the agent/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^stop$/i })).not.toBeInTheDocument();
    const composer = screen.getByLabelText(/message the agent/i);
    expect(composer).not.toBeDisabled();
    await userEvent.type(composer, "start the retest");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(client.submitMessage).toHaveBeenCalledWith(1, "start the retest");
  });

  it("shows Stop while running and pauses the session (#150)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "curl http://lab", rationale: "probe", tool_call_id: "abc" },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });
    vi.mocked(client.stopSession).mockResolvedValue({ status: "accepted" });
    renderAt(1);
    await userEvent.click(screen.getByRole("button", { name: /^stop$/i }));
    expect(client.stopSession).toHaveBeenCalledWith(1);
  });

  it("offers no Resume on a stopped session — a message picks it back up (#163)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "plan_updated", payload: { steps: ["step"] } }],
      status: "stopped",
      verdict: null,
      connected: true,
      thinking: "",
    });
    mockRecord({ status: "stopped" });
    vi.mocked(client.submitMessage).mockResolvedValue({ status: "accepted" });
    renderAt(1);

    expect(screen.getByLabelText(/stopped/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^resume$/i })).not.toBeInTheDocument();
    // The message input stays usable while stopped (the sandbox is alive), and
    // sending is what resumes the run — the server wakes it on receipt.
    const composer = screen.getByLabelText(/message the agent/i);
    expect(composer).not.toBeDisabled();
    await userEvent.type(composer, "keep going");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(client.submitMessage).toHaveBeenCalledWith(1, "keep going");
  });

  it("concludes manually mid-session, not only when paused for guidance (#150)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: { command: "curl http://lab", rationale: "probe", tool_call_id: "abc" },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });
    vi.mocked(client.concludeSession).mockResolvedValue({ status: "accepted" });
    renderAt(1);

    await userEvent.click(screen.getByRole("button", { name: /conclude/i }));
    await userEvent.selectOptions(screen.getByLabelText(/conclude status/i), "still_open");
    await userEvent.type(screen.getByLabelText(/conclude rationale/i), "seen enough");
    await userEvent.click(screen.getByRole("button", { name: /record verdict/i }));
    expect(client.concludeSession).toHaveBeenCalledWith(1, "still_open", "seen enough");
  });

  it("shows the agent-chosen timeout on a proposed command (#150)", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [
        {
          seq: 1,
          kind: "command_proposed",
          payload: {
            command: "nmap -Pn --top-ports 100 lab",
            rationale: "scan",
            tool_call_id: "abc",
            timeout_seconds: 120,
          },
        },
      ],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });
    renderAt(1);
    expect(screen.getByText(/runs up to 120s/i)).toBeInTheDocument();
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
      thinking: "",
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

  it("keeps the conversation and its composer inside one chat panel (#157)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "agent_message", payload: { text: "probing the endpoint" } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });

    renderAt(1);

    // The composer is welded to the transcript's bottom edge inside the same
    // panel, so the thread and the box you type into read as one chat rather
    // than two stacked boxes. Assert the DOM relationship, not the styling:
    // the log and the input must share a single <section> (Panel) ancestor.
    const log = screen.getByRole("log", { name: /agent conversation/i });
    const composer = await screen.findByLabelText(/message the agent/i);
    const panel = log.closest("section");

    expect(panel).not.toBeNull();
    expect(panel).toContainElement(composer);
  });

  it("renders the conclude form in-thread rather than as a detached panel (#157)", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [{ seq: 1, kind: "agent_message", payload: { text: "probing" } }],
      status: "awaiting_command",
      verdict: null,
      connected: true,
      thinking: "",
    });

    renderAt(1);

    await userEvent.click(screen.getByRole("button", { name: /conclude/i }));

    // The form appears where the conversation left off — inside the scrolling
    // log — instead of above the whole console as its own panel.
    const log = screen.getByRole("log", { name: /agent conversation/i });
    expect(log).toContainElement(screen.getByLabelText(/conclude rationale/i));
  });
});
