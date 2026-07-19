import { useQuery } from "@tanstack/react-query";

import { listRetestSessions } from "../api/client";
import { queryKeys } from "./queryKeys";

const TERMINAL = new Set(["concluded", "given_up", "ended", "error"]);

/** A finding's retest sessions, newest first; polls while any is non-terminal. */
export function useFindingSessions(findingId: number, enabled = true) {
  return useQuery({
    queryKey: queryKeys.findingSessions(findingId),
    queryFn: () => listRetestSessions(findingId),
    enabled: enabled && Number.isFinite(findingId),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((s) => !TERMINAL.has(s.status)) ? 2000 : false,
  });
}
