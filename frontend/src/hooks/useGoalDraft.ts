import { useQuery } from "@tanstack/react-query";

import { draftGoal } from "../api/client";

/** Generate + cache a pre-start goal draft for a finding (FR-17 6b-iii-b). */
export function useGoalDraft(findingId: number) {
  return useQuery({
    queryKey: ["goalDraft", findingId],
    queryFn: () => draftGoal(findingId),
    enabled: Number.isFinite(findingId),
    staleTime: Infinity, // stable until an explicit Regenerate (refetch)
    refetchOnWindowFocus: false,
  });
}
