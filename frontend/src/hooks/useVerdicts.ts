import { useQuery } from "@tanstack/react-query";

import { listVerdicts } from "../api/client";
import { queryKeys } from "./queryKeys";

/** All verdicts across every finding (filtered client-side where needed). */
export function useVerdicts(enabled = true) {
  return useQuery({
    queryKey: queryKeys.verdicts,
    queryFn: listVerdicts,
    enabled,
  });
}
