import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { Note } from "../api/types";
import { renderWithProviders } from "../test/utils";
import { NotesThread } from "./NotesThread";

vi.mock("../api/client");

function note(id: number, stage: Note["stage"], body: string): Note {
  return { id, finding_id: 7, stage, body, author: "user", created_at: "2026-07-16T10:00:00Z" };
}

describe("NotesThread", () => {
  beforeEach(() => {
    vi.mocked(client.listNotes).mockReset();
    vi.mocked(client.addNote).mockReset();
    vi.mocked(client.listNotes).mockResolvedValue([
      note(2, "verdict", "still open"),
      note(1, "goal", "check /admin"),
    ]);
    vi.mocked(client.addNote).mockResolvedValue(note(3, "goal", "new note"));
  });

  it("shows only this stage's notes when scoped to a stage", async () => {
    renderWithProviders(<NotesThread findingId={7} stage="goal" scope="stage" />);
    expect(await screen.findByText("check /admin")).toBeInTheDocument();
    expect(screen.queryByText("still open")).not.toBeInTheDocument();
  });

  it("shows the whole thread when scoped to all", async () => {
    renderWithProviders(<NotesThread findingId={7} stage="extract" scope="all" />);
    expect(await screen.findByText("check /admin")).toBeInTheDocument();
    expect(screen.getByText("still open")).toBeInTheDocument();
  });

  it("adds a note tagged with the stage", async () => {
    renderWithProviders(<NotesThread findingId={7} stage="goal" scope="stage" />);
    await screen.findByText("check /admin");
    await userEvent.type(screen.getByLabelText(/add a note on the goal stage/i), "hi");
    await userEvent.click(screen.getByRole("button", { name: /add note/i }));
    await waitFor(() => {
      expect(client.addNote).toHaveBeenCalledWith(7, "goal", "hi");
    });
  });
});
