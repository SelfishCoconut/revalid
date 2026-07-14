import { Link } from "react-router-dom";

import type { VerdictStatus } from "../api/types";
import { Spinner } from "../components/Spinner";
import { StatusBadge } from "../components/StatusBadge";
import { UploadReport } from "../components/UploadReport";
import { useReports } from "../hooks/useReports";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage, formatDateTime } from "../lib/format";

const SUMMARY: { status: VerdictStatus; label: string }[] = [
  { status: "still_open", label: "Still open" },
  { status: "fixed", label: "Fixed" },
  { status: "inconclusive", label: "Inconclusive" },
];

function VerdictSummary() {
  const verdicts = useVerdicts();
  const counts = new Map<VerdictStatus, number>();
  for (const verdict of verdicts.data ?? []) {
    counts.set(verdict.status, (counts.get(verdict.status) ?? 0) + 1);
  }

  return (
    <div className="grid grid-cols-3 gap-3">
      {SUMMARY.map(({ status, label }) => (
        <div key={status} className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="text-2xl font-semibold text-slate-800">
            {counts.get(status) ?? 0}
          </div>
          <div className="mt-1 flex items-center gap-2 text-sm text-slate-500">
            <StatusBadge status={status} />
            <span>{label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function ReportsOverview() {
  const reports = useReports();
  const ordered = [...(reports.data ?? [])].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime() ||
      b.id - a.id,
  );

  return (
    <div className="space-y-6">
      <section className="grid gap-4 lg:grid-cols-[2fr_3fr]">
        <UploadReport />
        <VerdictSummary />
      </section>

      <section>
        <h2 className="mb-3 text-base font-semibold text-slate-800">Reports</h2>
        {reports.isPending ? (
          <Spinner label="Loading reports" />
        ) : reports.isError ? (
          <p role="alert" className="text-sm text-red-700">
            {errorMessage(reports.error)}
          </p>
        ) : ordered.length === 0 ? (
          <p className="text-sm text-slate-500">No reports yet — upload a PDF to begin.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2">Filename</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Findings</th>
                  <th className="px-4 py-2">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ordered.map((report) => (
                  <tr key={report.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2">
                      <Link
                        to={`/reports/${String(report.id)}`}
                        className="font-medium text-sky-700 hover:underline"
                      >
                        {report.filename}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <StatusBadge status={report.status} />
                    </td>
                    <td className="px-4 py-2 text-slate-700">{report.finding_count}</td>
                    <td className="px-4 py-2 text-slate-500">
                      {formatDateTime(report.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
