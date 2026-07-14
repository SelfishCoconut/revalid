import type { VerdictStatus } from "../api/types";

const VERDICT_RING: Record<VerdictStatus, string> = {
  still_open: "ring-danger/50",
  inconclusive: "ring-warn/50",
  fixed: "ring-ok/50",
};
const VERDICT_FILL: Record<VerdictStatus, string> = {
  still_open: "bg-danger",
  inconclusive: "bg-warn",
  fixed: "bg-ok",
};

const STAGES = ["extract", "plan", "approve", "retest", "verdict"];

/**
 * The revalidation pipeline as a live state track. Every finding travels the
 * same fixed sequence — extract → plan → approve → retest → verdict — so the
 * order carries real meaning; this shows how far *this* finding has reached.
 * The final node borrows the verdict's colour once a determination exists.
 */
export function PipelineTrack({
  planned,
  approved,
  retested,
  verdict,
}: {
  planned: boolean;
  approved: boolean;
  retested: boolean;
  verdict?: VerdictStatus;
}) {
  // Cumulative + monotonic: a later state implies the earlier ones happened.
  const reached = [
    true,
    planned || approved || retested,
    approved || retested,
    retested,
    retested,
  ];
  const current = reached.lastIndexOf(true);

  return (
    <div className="overflow-x-auto px-1 py-1">
      <div className="relative mx-auto min-w-[26rem]">
        {/* base rail + progress fill, pinned to the node centres (10%…90%) */}
        <div className="absolute inset-x-[10%] top-[13px] h-px bg-line" />
        <div
          className="rev-grow absolute top-[13px] left-[10%] h-px bg-iris/60"
          style={{ width: `${String(current * 20)}%` }}
        />

        <ol className="relative grid grid-cols-5">
          {STAGES.map((stage, i) => {
            const isReached = reached[i];
            const isCurrent = i === current;
            let ring = "ring-line";
            let fill = "bg-line-2";
            if (isReached && i === 4 && verdict) {
              ring = VERDICT_RING[verdict];
              fill = VERDICT_FILL[verdict];
            } else if (isReached) {
              ring = "ring-iris/50";
              fill = "bg-iris";
            }
            return (
              <li key={stage} className="flex flex-col items-center gap-2">
                <span
                  className={`grid size-7 place-items-center rounded-full bg-panel-2 ring-1 ring-inset ${ring}`}
                >
                  <span
                    className={`size-2.5 rounded-full ${fill} ${isCurrent ? "rev-live" : ""}`}
                  />
                </span>
                <span
                  className={`font-mono text-[10px] uppercase tracking-[0.14em] ${isReached ? "text-dim" : "text-faint"}`}
                >
                  {stage}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
