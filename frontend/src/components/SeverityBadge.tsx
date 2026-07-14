import type { Severity } from "../api/types";

const STYLES: Record<Severity, string> = {
  critical: "text-danger-fg bg-danger/12 ring-danger/30",
  high: "text-high-fg bg-high/12 ring-high/30",
  medium: "text-warn-fg bg-warn/12 ring-warn/30",
  low: "text-low-fg bg-low/12 ring-low/30",
  info: "text-faint bg-faint/10 ring-faint/20",
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
