import type { VerdictStatus } from "../api/types";
import { pipelineReach } from "../lib/selectors";
import { TONE_FILL, TONE_RING, VERDICT_TONE } from "../lib/status";

const STAGES = ["extract", "plan", "approve", "retest", "verdict"] as const;

export type Stage = (typeof STAGES)[number];

/** Optional "go back" action per stage; a reached stage with one becomes clickable. */
export type StageBackActions = Partial<Record<Stage, () => void>>;

/**
 * The revalidation pipeline as a live state track. Every finding travels the
 * same fixed sequence — extract → plan → approve → retest → verdict — so the
 * order carries real meaning; this shows how far *this* finding has reached
 * (see {@link pipelineReach}). The final node borrows the verdict's colour once
 * a determination exists.
 *
 * The circles double as a stepper: a *reached* stage that is given a
 * back-action in {@link StageBackActions} renders as a focusable button that
 * steps the finding back to that stage (ADR-0023) — the caller decides which
 * ones are available (e.g. `approve` un-approves, `plan` regenerates).
 */
export function PipelineTrack({
  planned,
  approved,
  retested,
  verdict,
  onStageBack,
}: {
  planned: boolean;
  approved: boolean;
  retested: boolean;
  verdict?: VerdictStatus;
  onStageBack?: StageBackActions;
}) {
  const { reached, furthest, current } = pipelineReach({ planned, approved, retested });

  return (
    <div className="overflow-x-auto px-1 py-1">
      <div className="relative mx-auto min-w-[26rem]">
        {/* base rail + progress fill, pinned to the node centres (10%…90%) */}
        <div className="absolute inset-x-[10%] top-[13px] h-px bg-line" />
        <div
          className="rev-grow absolute top-[13px] left-[10%] h-px bg-iris/60"
          style={{ width: `${String(furthest * 20)}%` }}
        />

        <ol className="relative grid grid-cols-5">
          {STAGES.map((stage, i) => {
            const isReached = reached[i];
            const isCurrent = i === current;
            const back = isReached ? onStageBack?.[stage] : undefined;
            let ring = "ring-line";
            let fill = "bg-line-2";
            if (isReached && i === 4 && verdict) {
              const tone = VERDICT_TONE[verdict];
              ring = TONE_RING[tone];
              fill = TONE_FILL[tone];
            } else if (isReached) {
              ring = TONE_RING.iris;
              fill = TONE_FILL.iris;
            } else if (isCurrent) {
              // The next action — the live node; the iris ring marks "you are here",
              // and the dot pulses below. The rail fill stops short of it (furthest),
              // so it reads as in-progress rather than done.
              ring = TONE_RING.iris;
              fill = TONE_FILL.iris;
            }
            const node = (
              <>
                <span
                  className={`grid size-7 place-items-center rounded-full bg-panel-2 ring-1 ring-inset ${ring} ${back ? "transition-transform group-hover:scale-110" : ""}`}
                >
                  <span
                    className={`size-2.5 rounded-full ${fill} ${isCurrent ? "rev-live" : ""}`}
                  />
                </span>
                <span
                  className={`font-mono text-[10px] uppercase tracking-[0.14em] ${isReached || isCurrent ? "text-dim" : "text-faint"} ${back ? "group-hover:text-fg" : ""}`}
                >
                  {stage}
                </span>
              </>
            );
            return (
              <li key={stage} className="flex flex-col items-center">
                {back ? (
                  <button
                    type="button"
                    onClick={back}
                    aria-label={`Step back to ${stage}`}
                    title={`Step back to ${stage}`}
                    className="group flex cursor-pointer flex-col items-center gap-2 rounded-md focus-visible:ring-2 focus-visible:ring-iris/50 focus-visible:outline-none"
                  >
                    {node}
                  </button>
                ) : (
                  <div className="flex flex-col items-center gap-2">{node}</div>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
