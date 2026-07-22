import type { Severity } from "../api/types";
import { SEVERITY_TONE, TONE_FILL, TONE_TEXT } from "../lib/status";

// Worst → best, read left to right — mirrors the determination ledger's reading.
const ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

const LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Informative",
};

/**
 * The risk profile: how the findings pulled from every report distribute across
 * severity, as a segmented bar over five mono readouts. Complements the
 * determination ledger — that reads outcomes, this reads the stakes going in.
 */
export function SeverityMeter({ counts }: { counts: Record<Severity, number> }) {
  const total = ORDER.reduce((sum, severity) => sum + counts[severity], 0);

  return (
    <div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-panel-2 ring-1 ring-inset ring-line">
        {total === 0 ? (
          <div className="h-full w-full bg-[repeating-linear-gradient(135deg,var(--color-line)_0,var(--color-line)_1px,transparent_1px,transparent_9px)]" />
        ) : (
          ORDER.map((severity) => {
            const value = counts[severity];
            if (value === 0) return null;
            return (
              <div
                key={severity}
                className={`rev-grow h-full ${TONE_FILL[SEVERITY_TONE[severity]]}`}
                style={{ width: `${String((value / total) * 100)}%` }}
              />
            );
          })
        )}
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-line/60 sm:grid-cols-5">
        {ORDER.map((severity) => {
          const tone = SEVERITY_TONE[severity];
          return (
            <div key={severity} className="bg-panel px-3 py-3">
              <dt className="flex items-center gap-2">
                <span aria-hidden="true" className={`size-1.5 rounded-full ${TONE_FILL[tone]}`} />
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-faint">
                  {LABEL[severity]}
                </span>
              </dt>
              <dd
                className={`mt-1.5 font-mono text-2xl font-semibold tabular-nums ${TONE_TEXT[tone]}`}
              >
                {counts[severity]}
              </dd>
            </div>
          );
        })}
      </dl>

      {total === 0 && (
        <p className="mt-3 font-mono text-xs text-faint">
          No findings yet. Upload a report to populate the risk profile.
        </p>
      )}
    </div>
  );
}
