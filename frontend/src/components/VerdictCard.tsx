import type { Verdict } from "../api/types";
import { EvidenceView } from "./EvidenceView";
import { StatusBadge } from "./StatusBadge";

/** One retest verdict with its reason, rationale and evidence drill-down. */
export function VerdictCard({ verdict }: { verdict: Verdict }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4">
      <header className="flex flex-wrap items-center gap-2">
        <StatusBadge status={verdict.status} />
        <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">
          {verdict.reason_code}
        </code>
        <span className="text-xs text-slate-500">probe: {verdict.probe_kind}</span>
        {verdict.plan_version != null && (
          <span className="text-xs text-slate-500">
            plan v{verdict.plan_version}
          </span>
        )}
      </header>
      <p className="mt-2 text-sm text-slate-700">{verdict.rationale}</p>
      <EvidenceView verdict={verdict} />
    </article>
  );
}
