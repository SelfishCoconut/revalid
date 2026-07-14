import { Link, useParams } from "react-router-dom";

import type { Verdict } from "../api/types";
import { SeverityBadge } from "../components/SeverityBadge";
import { Spinner } from "../components/Spinner";
import { StatusBadge } from "../components/StatusBadge";
import { useFindings } from "../hooks/useFindings";
import { useReport } from "../hooks/useReports";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage } from "../lib/format";

/** Latest verdict for a finding = highest id among that finding's verdicts. */
function latestVerdict(verdicts: Verdict[], findingId: number): Verdict | undefined {
  return verdicts
    .filter((verdict) => verdict.finding_id === findingId)
    .reduce<Verdict | undefined>(
      (latest, verdict) => (!latest || verdict.id > latest.id ? verdict : latest),
      undefined,
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
      <p role="alert" className="text-sm text-red-700">
        {errorMessage(report.error)}
      </p>
    );
  }

  const data = report.data;

  return (
    <div className="space-y-6">
      <Link to="/" className="text-sm text-sky-700 hover:underline">
        ← All reports
      </Link>

      <header className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-lg font-semibold text-slate-800">{data.filename}</h1>
          <StatusBadge status={data.status} />
        </div>
        <p className="mt-1 text-sm text-slate-500">model: {data.model}</p>
        {data.status === "extracting" && (
          <div className="mt-3">
            <Spinner label="Extracting findings" />
          </div>
        )}
        {data.status === "failed" && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            Extraction failed: {data.error ?? "unknown error"}
          </p>
        )}
      </header>

      {ready && (
        <section>
          <h2 className="mb-3 text-base font-semibold text-slate-800">
            Findings ({data.finding_count})
          </h2>
          {findings.isPending ? (
            <Spinner label="Loading findings" />
          ) : findings.isError ? (
            <p role="alert" className="text-sm text-red-700">
              {errorMessage(findings.error)}
            </p>
          ) : (findings.data ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">No findings extracted.</p>
          ) : (
            <ul className="space-y-2">
              {(findings.data ?? []).map((finding) => {
                const latest = latestVerdict(verdicts.data ?? [], finding.id);
                return (
                  <li
                    key={finding.id}
                    className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3"
                  >
                    <SeverityBadge severity={finding.severity} />
                    <Link
                      to={`/findings/${String(finding.id)}`}
                      className="font-medium text-sky-700 hover:underline"
                    >
                      {finding.title}
                    </Link>
                    {latest && (
                      <span className="ml-auto">
                        <StatusBadge status={latest.status} />
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
