import type { Verdict, VerdictStatus } from "../api/types";
import { EvidenceView } from "./EvidenceView";
import { StatusBadge } from "./StatusBadge";

// Left-edge accent echoes the determination without re-stating it in words.
const ACCENT: Record<VerdictStatus, string> = {
  still_open: "before:bg-danger",
  inconclusive: "before:bg-warn",
  fixed: "before:bg-ok",
};

/** One retest verdict with its reason, rationale and evidence drill-down. */
export function VerdictCard({ verdict }: { verdict: Verdict }) {
  return (
    <article
      className={`relative overflow-hidden rounded-xl border border-line bg-panel/80 p-4 pl-5 before:absolute before:inset-y-0 before:left-0 before:w-1 ${ACCENT[verdict.status]}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <StatusBadge status={verdict.status} />
        <code className="rounded-md bg-panel-2 px-2 py-0.5 font-mono text-[11px] text-dim ring-1 ring-inset ring-line">
          {verdict.reason_code}
        </code>
        <span className="font-mono text-[11px] text-faint">probe: {verdict.probe_kind}</span>
        {verdict.plan_version != null && (
          <span className="font-mono text-[11px] text-faint">
            plan v{verdict.plan_version}
          </span>
        )}
      </header>
      <p className="mt-2.5 text-sm leading-relaxed text-dim">{verdict.rationale}</p>
      <EvidenceView verdict={verdict} />
    </article>
  );
}
