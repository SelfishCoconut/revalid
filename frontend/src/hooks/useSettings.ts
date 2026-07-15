import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSettings, probeProvider, updateSettings } from "../api/client";
import type { ProbeInput, ProbeResult, Settings, SettingsUpdate } from "../api/types";
import { queryKeys } from "./queryKeys";

/** The current model/provider setting (the API key is masked). */
export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: getSettings });
}

/** Persist a new setting; on success refresh the settings query. */
export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation<Settings, Error, SettingsUpdate>({
    mutationFn: updateSettings,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });
}

/** Probe a provider base URL for reachability + model list (the Test button). */
export function useProbeProvider() {
  return useMutation<ProbeResult, Error, ProbeInput>({ mutationFn: probeProvider });
}
