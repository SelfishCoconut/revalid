import { Link } from "react-router-dom";

import { DeterminationMeter } from "../components/DeterminationMeter";
import { Spinner } from "../components/Spinner";
import { StatusBadge } from "../components/StatusBadge";
import { UploadReport } from "../components/UploadReport";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { useReports } from "../hooks/useReports";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage, formatDateTime } from "../lib/format";
import { verdictCounts } from "../lib/selectors";

function Hero() {
  const verdicts = useVerdicts();
  const counts = verdictCounts(verdicts.data ?? []);

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
        <Eyebrow>Determination ledger · all retests</Eyebrow>
        <div className="mt-4">
          <DeterminationMeter counts={counts} />
        </div>
      </div>
    </Panel>
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
    <div className="rev-rise space-y-6">
      <Hero />

      <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
        <UploadReport />

        <Panel>
          <PanelHeader
            eyebrow="Reports"
            aside={
              <span className="font-mono text-[11px] text-faint">
                {ordered.length} total
              </span>
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
              No reports yet. Drop a pentest PDF to begin.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[34rem] text-left">
                <thead>
                  <tr className="border-b border-line font-mono text-[10px] uppercase tracking-[0.16em] text-faint">
                    <th className="px-4 py-2.5 font-medium">Filename</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                    <th className="px-4 py-2.5 font-medium">Findings</th>
                    <th className="px-4 py-2.5 font-medium">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/60">
                  {ordered.map((report) => (
                    <tr key={report.id} className="transition-colors hover:bg-panel-2/40">
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
                        {formatDateTime(report.created_at)}
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
