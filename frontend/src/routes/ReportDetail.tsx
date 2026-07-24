import { Link, useParams } from "react-router-dom";

import { DeterminationMeter } from "../components/DeterminationMeter";
import { ReportMetadataPanel } from "../components/ReportMetadataPanel";
import { SeverityBadge } from "../components/SeverityBadge";
import { SeverityMeter } from "../components/SeverityMeter";
import { Spinner } from "../components/Spinner";
import { StatusBadge } from "../components/StatusBadge";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { useFindings } from "../hooks/useFindings";
import { useCancelReport, useReport } from "../hooks/useReports";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage } from "../lib/format";
import { latestVerdict, severityCounts, verdictCounts, verdictsFor } from "../lib/selectors";

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
  const cancel = useCancelReport();
  const ready = report.data?.status === "ready";
  // A cancelled report (issue #205) keeps the findings extracted before the stop,
  // so its findings list is worth showing even though it never reached `ready`.
  const kept = report.data?.status === "cancelled";
  const showFindings = ready || kept;

  const findings = useFindings(reportId, showFindings);
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
  // Per-report determination roll-up (issue #130): one current verdict per
  // finding in this report, aggregated into the still-open/inconclusive/fixed
  // meter that used to live on each finding's Verdict page.
  const reportFindings = findings.data ?? [];
  const reportVerdicts = verdictsFor(verdicts.data ?? [], reportFindings);

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
          <div className="mt-4 flex items-center justify-between gap-3 border-t border-line pt-4">
            <Spinner label="Extracting findings" />
            {/* Stop extraction (issue #205): the run settles to `cancelled` at the
                next finding candidate, keeping whatever was extracted so far. */}
            <Button
              variant="ghost"
              disabled={cancel.isPending}
              onClick={() => {
                cancel.mutate(reportId);
              }}
            >
              Stop
            </Button>
          </div>
        )}
        {data.status === "failed" && (
          <p role="alert" className="mt-4 border-t border-line pt-4 text-sm text-danger-fg">
            Extraction failed: {data.error ?? "unknown error"}
          </p>
        )}
        {kept && (
          <p className="mt-4 border-t border-line pt-4 text-sm text-warn-fg">
            Extraction stopped by you — kept {data.finding_count}{" "}
            {data.finding_count === 1 ? "finding" : "findings"} extracted before the stop.
          </p>
        )}
      </Panel>

      {ready && <ReportMetadataPanel report={data} />}

      {ready && reportFindings.length > 0 && (
        <Panel className="overflow-hidden">
          <div className="p-5">
            <Eyebrow>Determinations</Eyebrow>
            <div className="mt-4">
              <DeterminationMeter counts={verdictCounts(reportVerdicts)} />
            </div>
          </div>
          {/* The overview's risk profile, scoped to this report (#161): what the
              stakes are here, read against the determinations right above it. */}
          <div className="border-t border-line bg-panel-2/30 p-5">
            <Eyebrow>Risk profile · severity in this report</Eyebrow>
            <div className="mt-4">
              <SeverityMeter counts={severityCounts(reportFindings)} />
            </div>
          </div>
        </Panel>
      )}

      {showFindings && (
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
          ) : reportFindings.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-faint">
              No findings extracted from this report.
            </p>
          ) : (
            <ul className="divide-y divide-line/60">
              {reportFindings.map((finding) => {
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
