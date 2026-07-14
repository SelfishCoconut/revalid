import { Link, useParams } from "react-router-dom";

import { SeverityBadge } from "../components/SeverityBadge";
import { Spinner } from "../components/Spinner";
import { StatusBadge } from "../components/StatusBadge";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { useFindings } from "../hooks/useFindings";
import { useReport } from "../hooks/useReports";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage } from "../lib/format";
import { latestVerdict } from "../lib/selectors";

function BackLink({ to, children }: { to: string; children: string }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1.5 font-mono text-[12px] text-faint transition-colors hover:text-dim"
    >
      <span aria-hidden="true">←</span>
      {children}
    </Link>
  );
}

export function ReportDetail() {
  const { id } = useParams();
  const reportId = Number(id);
  const report = useReport(reportId);
  const ready = report.data?.status === "ready";

  const findings = useFindings(reportId, ready);
  const verdicts = useVerdicts(ready);

  if (report.isPending) {
    return <Spinner label="Loading report" />;
  }
  if (report.isError) {
    return (
      <p role="alert" className="text-sm text-danger-fg">
        {errorMessage(report.error)}
      </p>
    );
  }

  const data = report.data;

  return (
    <div className="rev-rise space-y-6">
      <BackLink to="/">All reports</BackLink>

      <Panel className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Eyebrow>Report</Eyebrow>
            <h1 className="mt-1.5 font-mono text-xl font-semibold text-fg">
              {data.filename}
            </h1>
            <p className="mt-1 font-mono text-[12px] text-faint">model: {data.model}</p>
          </div>
          <StatusBadge status={data.status} />
        </div>
        {data.status === "extracting" && (
          <div className="mt-4 border-t border-line pt-4">
            <Spinner label="Extracting findings" />
          </div>
        )}
        {data.status === "failed" && (
          <p role="alert" className="mt-4 border-t border-line pt-4 text-sm text-danger-fg">
            Extraction failed: {data.error ?? "unknown error"}
          </p>
        )}
      </Panel>

      {ready && (
        <Panel>
          <PanelHeader
            eyebrow="Findings"
            aside={
              <span className="font-mono text-[11px] text-faint">
                {data.finding_count} extracted
              </span>
            }
          />
          {findings.isPending ? (
            <div className="px-4 py-6">
              <Spinner label="Loading findings" />
            </div>
          ) : findings.isError ? (
            <p role="alert" className="px-4 py-6 text-sm text-danger-fg">
              {errorMessage(findings.error)}
            </p>
          ) : (findings.data ?? []).length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-faint">
              No findings extracted from this report.
            </p>
          ) : (
            <ul className="divide-y divide-line/60">
              {(findings.data ?? []).map((finding) => {
                const latest = latestVerdict(verdicts.data ?? [], finding.id);
                return (
                  <li key={finding.id}>
                    <Link
                      to={`/findings/${String(finding.id)}`}
                      className="flex flex-wrap items-center gap-3 px-4 py-3 transition-colors hover:bg-panel-2/40"
                    >
                      <SeverityBadge severity={finding.severity} />
                      <span className="font-medium text-fg">{finding.title}</span>
                      {latest && (
                        <span className="ml-auto">
                          <StatusBadge status={latest.status} />
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
      )}
    </div>
  );
}
