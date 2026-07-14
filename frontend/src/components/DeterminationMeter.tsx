import type { VerdictStatus } from "../api/types";
import { STATUS_META, TONE_FILL, TONE_TEXT, VERDICT_TONE } from "../lib/status";

// Worst → best, read left to right: the instrument's aggregate reading.
const ORDER: VerdictStatus[] = ["still_open", "inconclusive", "fixed"];

/**
 * The determination ledger: one aggregate reading of every retest run so far,
 * as a segmented instrument bar over three mono readouts. The signature moment
 * of the overview — the tool exists to move findings from red to green.
 */
export function DeterminationMeter({
  counts,
}: {
  counts: Record<VerdictStatus, number>;
}) {
  const total = ORDER.reduce((sum, status) => sum + counts[status], 0);

  return (
    <div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-panel-2 ring-1 ring-inset ring-line">
        {total === 0 ? (
          <div className="h-full w-full bg-[repeating-linear-gradient(135deg,var(--color-line)_0,var(--color-line)_1px,transparent_1px,transparent_9px)]" />
        ) : (
          ORDER.map((status) => {
            const value = counts[status];
            if (value === 0) return null;
            return (
              <div
                key={status}
                className={`rev-grow h-full ${TONE_FILL[VERDICT_TONE[status]]}`}
                style={{ width: `${String((value / total) * 100)}%` }}
              />
            );
          })
        )}
      </div>

      <dl className="mt-5 grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-line/60">
        {ORDER.map((status) => {
          const tone = VERDICT_TONE[status];
          return (
            <div key={status} className="bg-panel px-4 py-3">
              <dt className="flex items-center gap-2">
                <span aria-hidden="true" className={`size-1.5 rounded-full ${TONE_FILL[tone]}`} />
                <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-faint">
                  {STATUS_META[status].label}
                </span>
              </dt>
              <dd className={`mt-1.5 font-mono text-3xl font-semibold tabular-nums ${TONE_TEXT[tone]}`}>
                {counts[status]}
              </dd>
            </div>
          );
        })}
      </dl>

      {total === 0 && (
        <p className="mt-3 font-mono text-xs text-faint">
          No determinations yet. Approve a plan and run a retest to record one.
        </p>
      )}
    </div>
  );
}
