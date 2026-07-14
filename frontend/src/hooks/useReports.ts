import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getReport, listReports, uploadReport } from "../api/client";
import type { Report } from "../api/types";
import { queryKeys } from "./queryKeys";

/** All reports (the overview table sorts newest-first at render time). */
export function useReports() {
  return useQuery({ queryKey: queryKeys.reports, queryFn: listReports });
}

/**
 * A single report. While the backend is still extracting findings the query
 * polls; polling stops automatically once the status is `ready` or `failed`.
 */
export function useReport(id: number) {
  return useQuery({
    queryKey: queryKeys.report(id),
    queryFn: () => getReport(id),
    refetchInterval: (query) =>
      query.state.data?.status === "extracting" ? 2000 : false,
    enabled: Number.isFinite(id),
  });
}

/** Upload a PDF; on success refresh the reports list. */
export function useUploadReport() {
  const client = useQueryClient();
  return useMutation<Report, Error, File>({
    mutationFn: uploadReport,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
}
