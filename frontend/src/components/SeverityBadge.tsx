import type { Severity } from "../api/types";

const STYLES: Record<Severity, string> = {
  critical: "text-danger bg-danger/12 ring-danger/25",
  high: "text-high bg-high/12 ring-high/25",
  medium: "text-warn bg-warn/12 ring-warn/25",
  low: "text-low bg-low/12 ring-low/25",
  info: "text-faint bg-white/5 ring-white/10",
};

/** Colour-coded pill for a finding's severity. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] ring-1 ring-inset ${STYLES[severity]}`}
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {severity}
    </span>
  );
}
