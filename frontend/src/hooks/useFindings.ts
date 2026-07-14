import { useQuery } from "@tanstack/react-query";

import { listFindings } from "../api/client";
import { queryKeys } from "./queryKeys";

/**
 * Findings, optionally scoped to a report. Pass `enabled: false` to defer the
 * request (e.g. while a report is still extracting).
 */
export function useFindings(reportId?: number, enabled = true) {
  return useQuery({
    queryKey: queryKeys.findings(reportId),
    queryFn: () => listFindings(reportId),
    enabled,
  });
}
