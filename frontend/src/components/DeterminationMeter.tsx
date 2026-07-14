import type { VerdictStatus } from "../api/types";

interface Segment {
  status: VerdictStatus;
  label: string;
  bar: string;
  text: string;
}

// Worst → best, read left to right: the instrument's aggregate reading.
const SEGMENTS: Segment[] = [
  { status: "still_open", label: "still open", bar: "bg-danger", text: "text-danger-fg" },
  { status: "inconclusive", label: "inconclusive", bar: "bg-warn", text: "text-warn-fg" },
  { status: "fixed", label: "fixed", bar: "bg-ok", text: "text-ok-fg" },
];

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
  const total = SEGMENTS.reduce((sum, { status }) => sum + counts[status], 0);

  return (
    <div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-panel-2 ring-1 ring-inset ring-line">
        {total === 0 ? (
          <div className="h-full w-full bg-[repeating-linear-gradient(135deg,var(--color-line)_0,var(--color-line)_1px,transparent_1px,transparent_9px)]" />
        ) : (
          SEGMENTS.map(({ status, bar }) => {
            const value = counts[status];
            if (value === 0) return null;
            return (
              <div
                key={status}
                className={`rev-grow h-full ${bar}`}
                style={{ width: `${String((value / total) * 100)}%` }}
              />
            );
          })
        )}
      </div>

      <dl className="mt-5 grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-line/60">
        {SEGMENTS.map(({ status, label, bar, text }) => (
          <div key={status} className="bg-panel px-4 py-3">
            <dt className="flex items-center gap-2">
              <span aria-hidden="true" className={`size-1.5 rounded-full ${bar}`} />
              <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-faint">
                {label}
              </span>
            </dt>
            <dd className={`mt-1.5 font-mono text-3xl font-semibold tabular-nums ${text}`}>
              {counts[status]}
            </dd>
          </div>
        ))}
      </dl>

      {total === 0 && (
        <p className="mt-3 font-mono text-xs text-faint">
          No determinations yet. Approve a plan and run a retest to record one.
        </p>
      )}
    </div>
  );
}
