import type { PlanStatus, ReportStatus, VerdictStatus } from "../api/types";

type Tone = "red" | "amber" | "green" | "gray" | "blue";

const TONE_STYLES: Record<Tone, string> = {
  red: "bg-red-100 text-red-800 ring-red-600/20",
  amber: "bg-amber-100 text-amber-800 ring-amber-600/20",
  green: "bg-green-100 text-green-800 ring-green-600/20",
  gray: "bg-slate-100 text-slate-700 ring-slate-500/20",
  blue: "bg-sky-100 text-sky-800 ring-sky-600/20",
};

type KnownStatus = ReportStatus | PlanStatus | VerdictStatus;

const STATUS_META: Record<KnownStatus, { tone: Tone; label: string }> = {
  // Report
  extracting: { tone: "blue", label: "extracting" },
  ready: { tone: "green", label: "ready" },
  failed: { tone: "red", label: "failed" },
  // Plan
  proposed: { tone: "amber", label: "proposed" },
  approved: { tone: "green", label: "approved" },
  rejected: { tone: "red", label: "rejected" },
  superseded: { tone: "gray", label: "superseded" },
  // Verdict
  still_open: { tone: "red", label: "still open" },
  fixed: { tone: "green", label: "fixed" },
  inconclusive: { tone: "amber", label: "inconclusive" },
};

/** Colour-coded pill for any known report/plan/verdict status string. */
export function StatusBadge({ status }: { status: KnownStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_STYLES[meta.tone]}`}
    >
      {meta.label}
    </span>
  );
}
