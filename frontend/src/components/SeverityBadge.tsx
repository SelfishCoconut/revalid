import type { Severity } from "../api/types";

const STYLES: Record<Severity, string> = {
  critical: "bg-red-100 text-red-800 ring-red-600/20",
  high: "bg-orange-100 text-orange-800 ring-orange-600/20",
  medium: "bg-amber-100 text-amber-800 ring-amber-600/20",
  low: "bg-sky-100 text-sky-800 ring-sky-600/20",
  info: "bg-slate-100 text-slate-700 ring-slate-500/20",
};

/** Colour-coded pill for a finding's severity. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium uppercase tracking-wide ring-1 ring-inset ${STYLES[severity]}`}
    >
      {severity}
    </span>
  );
}
