import { useState } from "react";
import { Link } from "react-router-dom";

import { DeterminationMeter } from "../components/DeterminationMeter";
import { ReportActions } from "../components/ReportActions";
import { SeverityMeter } from "../components/SeverityMeter";
import { Spinner } from "../components/Spinner";
import { StatusBadge } from "../components/StatusBadge";
import { UploadReport } from "../components/UploadReport";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { useFindings } from "../hooks/useFindings";
import { useReports } from "../hooks/useReports";
import { useVerdicts } from "../hooks/useVerdicts";
import { formatDate, useDateFormat } from "../lib/dateFormat";
import { errorMessage } from "../lib/format";
import {
  findingsNotArchived,
  severityCounts,
  verdictCounts,
  verdictsFor,
} from "../lib/selectors";

function Hero() {
  const verdicts = useVerdicts();
  const findings = useFindings();
  // Both meters read the active workspace only: an archived report is shelved,
  // so its findings must stop inflating the ledger and the risk profile (#162).
  const archived = useReports(true);
  const active = findingsNotArchived(findings.data ?? [], archived.data ?? []);
  const counts = verdictCounts(verdictsFor(verdicts.data ?? [], active));
  const severity = severityCounts(active);

  return (
    <Panel className="overflow-hidden">
      <div className="px-6 pt-7 pb-6 sm:px-8">
        <Eyebrow>AI-driven revalidation</Eyebrow>
        <h1 className="mt-3 max-w-2xl text-3xl leading-[1.12] font-semibold tracking-tight text-fg sm:text-[2.6rem]">
          Does the finding still hold?
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-dim">
          Load a pentest report, approve a safety-gated retest against the lab, and get a
          clear determination for every finding — still open, fixed, or inconclusive —
          backed by the request and response that prove it.
        </p>
      </div>
      <div className="border-t border-line bg-panel-2/30 px-6 pt-5 pb-6 sm:px-8">
        <Eyebrow>Determination ledger · one per finding</Eyebrow>
        <div className="mt-4">
          <DeterminationMeter counts={counts} />
        </div>
      </div>
      <div className="border-t border-line bg-panel-2/30 px-6 pt-5 pb-6 sm:px-8">
        <Eyebrow>Risk profile · severity across active reports</Eyebrow>
        <div className="mt-4">
          <SeverityMeter counts={severity} />
        </div>
      </div>
    </Panel>
  );
}

export function ReportsOverview() {
  const [showArchived, setShowArchived] = useState(false);
  const dateFormat = useDateFormat();
  const reports = useReports(showArchived);
  const ordered = [...(reports.data ?? [])].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime() ||
      b.id - a.id,
  );

  return (
    <div className="rev-rise space-y-6">
      <Hero />

      <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
        <div className="space-y-3">
          <UploadReport />
          <Link
            to="/new"
            className="flex items-center justify-center gap-2 rounded-lg border border-line bg-panel/60 px-4 py-3 font-mono text-[13px] font-semibold text-dim transition-colors hover:border-iris/50 hover:text-fg"
          >
            + Create a report manually
          </Link>
        </div>

        <Panel>
          <PanelHeader
            eyebrow="Reports"
            aside={
              <div className="flex items-center gap-0.5 rounded-lg border border-line bg-panel-2/40 p-0.5 font-mono text-[11px]">
                {([false, true] as const).map((archived) => (
                  <button
                    key={String(archived)}
                    type="button"
                    onClick={() => {
                      setShowArchived(archived);
                    }}
                    className={`rounded-md px-2.5 py-1 transition-colors ${
                      showArchived === archived
                        ? "bg-panel text-fg"
                        : "text-faint hover:text-dim"
                    }`}
                  >
                    {archived ? "Archived" : "Active"}
                  </button>
                ))}
              </div>
            }
          />
          {reports.isPending ? (
            <div className="px-4 py-6">
              <Spinner label="Loading reports" />
            </div>
          ) : reports.isError ? (
            <p role="alert" className="px-4 py-6 text-sm text-danger-fg">
              {errorMessage(reports.error)}
            </p>
          ) : ordered.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-faint">
              {showArchived
                ? "No archived reports."
                : "No reports yet. Drop a pentest PDF to begin."}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[38rem] text-left">
                <thead>
                  <tr className="border-b border-line font-mono text-[10px] uppercase tracking-[0.16em] text-faint">
                    <th className="px-4 py-2.5 font-medium">Filename</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                    <th className="px-4 py-2.5 font-medium">Findings</th>
                    <th className="px-4 py-2.5 font-medium">Created</th>
                    <th className="px-4 py-2.5 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/60">
                  {ordered.map((report) => (
                    <tr key={report.id} className="group transition-colors hover:bg-panel-2/40">
                      <td className="px-4 py-3">
                        <Link
                          to={`/reports/${String(report.id)}`}
                          className="font-mono text-[13px] font-medium text-fg underline-offset-4 hover:text-iris-fg hover:underline"
                        >
                          {report.filename}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={report.status} />
                      </td>
                      <td className="px-4 py-3 font-mono text-[13px] tabular-nums text-dim">
                        {report.finding_count}
                      </td>
                      <td className="px-4 py-3 font-mono text-[13px] text-faint">
                        {formatDate(report.created_at, dateFormat)}
                      </td>
                      <td className="px-4 py-3">
                        <ReportActions
                          report={report}
                          className="justify-end opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
