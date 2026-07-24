import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelReport,
  createManualReport,
  deleteReport,
  getReport,
  listReports,
  setReportArchived,
  updateReportMetadata,
  uploadReport,
} from "../api/client";
import type { ManualReportInput, Report, ReportMetadata } from "../api/types";
import { queryKeys } from "./queryKeys";

/** Reports for the overview — active by default, archived when asked (#128). */
export function useReports(archived = false) {
  return useQuery({
    queryKey: [...queryKeys.reports, "list", archived],
    queryFn: () => listReports(archived),
  });
}

/** Archive or unarchive a report; on success refresh every report list (#128). */
export function useSetReportArchived() {
  const client = useQueryClient();
  return useMutation<Report, Error, { id: number; archived: boolean }>({
    mutationFn: ({ id, archived }) => setReportArchived(id, archived),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
}

/** Stop an in-flight extraction (issue #205); on success refresh that report + lists. */
export function useCancelReport() {
  const client = useQueryClient();
  return useMutation<Report, Error, number>({
    mutationFn: cancelReport,
    onSuccess: (report) => {
      void client.invalidateQueries({ queryKey: queryKeys.report(report.id) });
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
}

/** Permanently delete a report (cascade); on success refresh every report list (#128). */
export function useDeleteReport() {
  const client = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: deleteReport,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
}

/** Save operator edits to a report's document metadata; refresh that report (#133). */
export function useUpdateReportMetadata(id: number) {
  const client = useQueryClient();
  return useMutation<Report, Error, ReportMetadata>({
    mutationFn: (metadata) => updateReportMetadata(id, metadata),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.report(id) });
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
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

/** Upload a PDF (optionally forcing past a duplicate); on success refresh the list. */
export function useUploadReport() {
  const client = useQueryClient();
  return useMutation<Report, Error, { file: File; force?: boolean }>({
    mutationFn: ({ file, force }) => uploadReport(file, force),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
}

/** Create a report by hand (form or JSON); on success refresh the reports list. */
export function useCreateManualReport() {
  const client = useQueryClient();
  return useMutation<Report, Error, ManualReportInput>({
    mutationFn: createManualReport,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.reports });
    },
  });
}
