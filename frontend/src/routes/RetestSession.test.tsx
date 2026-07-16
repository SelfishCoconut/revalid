import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import * as hook from "../hooks/useRetestSession";
import { renderWithProviders } from "../test/utils";
import { RetestSession } from "./RetestSession";

vi.mock("../hooks/useRetestSession");
vi.mock("../api/client");

function renderAt(id = 1) {
  return renderWithProviders(
    <Routes>
      <Route path="/retest-sessions/:id" element={<RetestSession />} />
    </Routes>,
    `/retest-sessions/${String(id)}`,
  );
}

describe("RetestSession", () => {
  beforeEach(() => {
    vi.resetAllMocks();
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

  it("runs a !-prefixed console line as a manual operator command", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });
    vi.mocked(client.submitHumanCommand).mockResolvedValue({ status: "accepted" });

    renderAt(1);

    await userEvent.type(screen.getByLabelText(/operator console input/i), "!whoami");
    await userEvent.click(screen.getByRole("button", { name: /run/i }));

    expect(client.submitHumanCommand).toHaveBeenCalledWith(1, "whoami");
  });

  it("treats non-! text as chat (hinted), not a command", async () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "awaiting_command",
      verdict: null,
      connected: true,
    });

    renderAt(1);

    await userEvent.type(screen.getByLabelText(/operator console input/i), "focus on login");
    expect(screen.getByRole("button", { name: /run/i })).toBeDisabled();
    expect(screen.getByText(/arrives in a later slice/i)).toBeInTheDocument();
    expect(client.submitHumanCommand).not.toHaveBeenCalled();
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

  it("disables the console input once the session is over", () => {
    vi.mocked(hook.useRetestSession).mockReturnValue({
      events: [],
      status: "concluded",
      verdict: { status: "still_open", rationale: "bypassable" },
      connected: true,
    });

    renderAt(1);

    expect(screen.getByLabelText(/operator console input/i)).toBeDisabled();
  });
});
