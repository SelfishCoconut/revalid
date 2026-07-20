import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { ProbeResult, Settings as SettingsData } from "../api/types";
import { renderWithProviders } from "../test/utils";
import Settings from "./Settings";

vi.mock("../api/client");

const current: SettingsData = {
  model: "ollama:qwen3.6:27b",
  base_url: "http://localhost:11434/v1",
  api_key_set: false,
  api_key_hint: null,
};

describe("Settings", () => {
  beforeEach(() => {
    vi.mocked(client.getSettings).mockReset();
    vi.mocked(client.updateSettings).mockReset();
    vi.mocked(client.probeProvider).mockReset();
    vi.mocked(client.getSettings).mockResolvedValue(current);
  });

  it("renders the current model and saves an edit", async () => {
    const saved: SettingsData = { ...current, model: "ollama:qwen3:14b" };
    vi.mocked(client.updateSettings).mockResolvedValue(saved);
    // Model choices are discovered (not hardcoded); the form auto-probes on
    // mount, so the host's models surface as radios without pressing Refresh.
    vi.mocked(client.probeProvider).mockResolvedValue({
      reachable: true,
      models: ["qwen3:14b"],
      error: null,
    });

    renderWithProviders(<Settings />, "/settings");

    // The current model is pre-selected as a radio in the group.
    const currentRadio = await screen.findByRole("radio", { name: "ollama:qwen3.6:27b" });
    expect(currentRadio).toBeChecked();

    // Switch to a discovered model by selecting its radio.
    await userEvent.click(await screen.findByRole("radio", { name: "ollama:qwen3:14b" }));

    const keyInput = screen.getByLabelText(/api key/i);
    await userEvent.type(keyInput, "sk-secret");

    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    // TanStack Query 5.101 passes a second (context) argument to mutationFn,
    // so assert the call happened and inspect the first arg rather than using
    // a strict single-argument toHaveBeenCalledWith.
    await waitFor(() => expect(client.updateSettings).toHaveBeenCalled());
    const [payload] = vi.mocked(client.updateSettings).mock.calls[0];
    expect(payload).toEqual({
      model: "ollama:qwen3:14b",
      base_url: "http://localhost:11434/v1",
      api_key: "sk-secret",
    });

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("refreshes and adds discovered models as radios", async () => {
    // Deliberately NOT in KNOWN_MODELS: if these ids show up as radios it can
    // only be because the discoveredModels/modelOptions merge (and the
    // looksLikeOllama ":11434" prefixing) actually ran — reusing a
    // KNOWN_MODELS id here would let a broken merge pass unnoticed.
    const probeResult: ProbeResult = {
      reachable: true,
      models: ["llama3.2:3b", "mistral:7b"],
      error: null,
    };
    vi.mocked(client.probeProvider).mockResolvedValue(probeResult);

    renderWithProviders(<Settings />, "/settings");
    await screen.findByRole("radio", { name: "ollama:qwen3.6:27b" });

    await userEvent.click(screen.getByRole("button", { name: /refresh models/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Reachable — 2 models discovered.",
    );
    expect(client.probeProvider).toHaveBeenCalled();
    const [probePayload] = vi.mocked(client.probeProvider).mock.calls[0];
    expect(probePayload).toEqual({ base_url: "http://localhost:11434/v1", api_key: null });

    // The base URL looks like Ollama, so discovered ids must be re-prefixed
    // with "ollama:" before landing in the radio group.
    expect(
      await screen.findByRole("radio", { name: "ollama:llama3.2:3b" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "ollama:mistral:7b" })).toBeInTheDocument();
  });

  it("surfaces an unreachable probe result", async () => {
    vi.mocked(client.probeProvider).mockResolvedValue({
      reachable: false,
      models: [],
      error: "connection refused",
    });

    renderWithProviders(<Settings />, "/settings");
    await screen.findByRole("radio", { name: "ollama:qwen3.6:27b" });
    await userEvent.click(screen.getByRole("button", { name: /refresh models/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Unreachable — connection refused",
    );
  });

  it("shows a masked hint for an existing key and lets base URL be edited", async () => {
    vi.mocked(client.getSettings).mockResolvedValue({
      ...current,
      api_key_set: true,
      api_key_hint: "3456",
    });

    renderWithProviders(<Settings />, "/settings");
    const keyInput = await screen.findByLabelText(/api key/i);
    expect(keyInput).toHaveAttribute("placeholder", expect.stringContaining("3456"));

    const baseUrlInput = screen.getByLabelText(/base url/i);
    await userEvent.clear(baseUrlInput);
    await userEvent.type(baseUrlInput, "http://other-host:11434/v1");
    expect(baseUrlInput).toHaveValue("http://other-host:11434/v1");
  });

  it("surfaces a load error instead of the form", async () => {
    vi.mocked(client.getSettings).mockRejectedValue(new Error("network down"));

    renderWithProviders(<Settings />, "/settings");

    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});
