import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import type { ProbeResult, Settings } from "../api/types";
import { useProbeProvider, useSettings, useUpdateSettings } from "./useSettings";

vi.mock("../api/client");

const maskedSettings: Settings = {
  model: "ollama:qwen3.6:27b",
  base_url: "http://localhost:11434/v1",
  api_key_set: false,
  api_key_hint: null,
};

/** A fresh QueryClient (no retries) wrapped for `renderHook`, mirroring the
 * settings used by `renderWithProviders` in `../test/utils`. */
function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useSettings", () => {
  beforeEach(() => {
    vi.mocked(client.getSettings).mockReset();
    vi.mocked(client.updateSettings).mockReset();
    vi.mocked(client.probeProvider).mockReset();
  });

  it("fetches and returns the masked setting", async () => {
    vi.mocked(client.getSettings).mockResolvedValue(maskedSettings);

    const { result } = renderHook(() => useSettings(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.data).toEqual(maskedSettings));
    expect(client.getSettings).toHaveBeenCalledTimes(1);
  });

  it("persists a new setting and refreshes the settings query", async () => {
    vi.mocked(client.getSettings).mockResolvedValue(maskedSettings);
    const updated: Settings = { ...maskedSettings, model: "openai:gpt-5" };
    vi.mocked(client.updateSettings).mockResolvedValue(updated);

    const wrapper = makeWrapper();
    const settings = renderHook(() => useSettings(), { wrapper });
    await waitFor(() => expect(settings.result.current.isSuccess).toBe(true));

    const mutation = renderHook(() => useUpdateSettings(), { wrapper });
    mutation.result.current.mutate({
      model: "openai:gpt-5",
      base_url: null,
    });

    await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true));
    expect(vi.mocked(client.updateSettings).mock.calls[0][0]).toEqual({
      model: "openai:gpt-5",
      base_url: null,
    });
    // Invalidating the settings query re-triggers `getSettings`.
    await waitFor(() => expect(client.getSettings).toHaveBeenCalledTimes(2));
  });

  it("probes a provider without invalidating the settings query", async () => {
    vi.mocked(client.getSettings).mockResolvedValue(maskedSettings);
    const probeResult: ProbeResult = { reachable: true, models: ["qwen3.6:27b"], error: null };
    vi.mocked(client.probeProvider).mockResolvedValue(probeResult);

    const wrapper = makeWrapper();
    const settings = renderHook(() => useSettings(), { wrapper });
    await waitFor(() => expect(settings.result.current.isSuccess).toBe(true));

    const probe = renderHook(() => useProbeProvider(), { wrapper });
    probe.result.current.mutate({ base_url: "http://h/v1", api_key: null });

    await waitFor(() => expect(probe.result.current.data).toEqual(probeResult));
    expect(client.getSettings).toHaveBeenCalledTimes(1);
  });
});
