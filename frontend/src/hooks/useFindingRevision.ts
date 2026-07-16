import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { editFinding, listFindingVersions } from "../api/client";
import type { Finding, FindingEdit } from "../api/types";
import { queryKeys } from "./queryKeys";

/** Full version history of a finding, oldest first (FR-16). */
export function useFindingVersions(findingId: number) {
  return useQuery({
    queryKey: queryKeys.findingVersions(findingId),
    queryFn: () => listFindingVersions(findingId),
    enabled: Number.isFinite(findingId),
  });
}

/**
 * Record an operator edit as a new immutable finding version (FR-16). Invalidates
 * both the finding lists (the current version changed) and the version history.
 */
export function useEditFinding(findingId: number) {
  const client = useQueryClient();
  return useMutation<Finding, Error, FindingEdit>({
    mutationFn: (body) => editFinding(findingId, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["findings"] });
      void client.invalidateQueries({ queryKey: queryKeys.findingVersions(findingId) });
    },
  });
}
