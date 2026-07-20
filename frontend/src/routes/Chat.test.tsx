import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { ChatDetail, ChatSummary } from "../api/types";
import { renderWithProviders } from "../test/utils";
import { Chat } from "./Chat";

vi.mock("../api/client");

function renderChat(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/chat" element={<Chat />} />
      <Route path="/chat/:id" element={<Chat />} />
    </Routes>,
    route,
  );
}

const THREAD: ChatSummary = {
  id: 1,
  title: "How many reports?",
  model: "test",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
};

function detail(messages: ChatDetail["messages"]): ChatDetail {
  return { ...THREAD, messages };
}

describe("Chat", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // jsdom has no layout engine; the auto-scroll effect calls this.
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(client.listChats).mockResolvedValue([]);
    vi.mocked(client.getChat).mockResolvedValue(detail([]));
    vi.mocked(client.createChat).mockResolvedValue({ ...THREAD, id: 5, title: "New chat" });
    vi.mocked(client.deleteChat).mockResolvedValue(undefined);
  });

  it("shows the empty state and starts a new chat", async () => {
    renderChat("/chat");
    // Centred empty pane offers a New chat button.
    const start = await screen.findByRole("button", { name: "New chat" });
    await userEvent.click(start);
    // Creating a thread navigates to it, mounting the conversation (getChat(5)).
    expect(client.createChat).toHaveBeenCalled();
    await waitFor(() => {
      expect(client.getChat).toHaveBeenCalledWith(5);
    });
  });

  it("renders a thread's transcript", async () => {
    vi.mocked(client.getChat).mockResolvedValue(
      detail([
        { id: 1, role: "user", content: "how many reports?", created_at: "" },
        { id: 2, role: "assistant", content: "There are 3 reports.", created_at: "" },
      ]),
    );
    renderChat("/chat/1");
    expect(await screen.findByText("how many reports?")).toBeInTheDocument();
    expect(await screen.findByText("There are 3 reports.")).toBeInTheDocument();
  });

  it("sends a typed question and shows the reply", async () => {
    vi.mocked(client.getChat).mockResolvedValue(detail([]));
    vi.mocked(client.sendChatMessage).mockResolvedValue(
      detail([
        { id: 1, role: "user", content: "how many criticals?", created_at: "" },
        { id: 2, role: "assistant", content: "Two critical findings.", created_at: "" },
      ]),
    );
    renderChat("/chat/1");

    const box = await screen.findByLabelText("Ask about the reports");
    await userEvent.type(box, "how many criticals?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(client.sendChatMessage).toHaveBeenCalledWith(1, "how many criticals?");
    expect(await screen.findByText("Two critical findings.")).toBeInTheDocument();
  });

  it("sends an example prompt on click from the empty conversation", async () => {
    vi.mocked(client.sendChatMessage).mockResolvedValue(detail([]));
    renderChat("/chat/1");
    const example = await screen.findByText("How many reports do we have?");
    await userEvent.click(example);
    expect(client.sendChatMessage).toHaveBeenCalledWith(1, "How many reports do we have?");
  });

  it("lists threads and deletes one", async () => {
    vi.mocked(client.listChats).mockResolvedValue([THREAD]);
    renderChat("/chat/1");
    // The thread appears in the rail (title rendered).
    expect(await screen.findAllByText("How many reports?")).not.toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Delete How many reports?" }));
    expect(client.deleteChat).toHaveBeenCalledWith(1);
  });
});
