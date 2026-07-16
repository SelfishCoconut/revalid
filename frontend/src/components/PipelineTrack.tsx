import { Link } from "react-router-dom";

import type { VerdictStatus } from "../api/types";
import { pipelineReach } from "../lib/selectors";
import { TONE_FILL, TONE_RING, VERDICT_TONE } from "../lib/status";

const STAGES = ["extract", "plan", "approve", "retest", "verdict"] as const;

export type Stage = (typeof STAGES)[number];

/**
 * The revalidation pipeline as a live, walkable stepper (ADR-0024). Every finding
 * travels the same fixed sequence — extract → plan → approve → retest → verdict —
 * so the order carries real meaning; this shows how far *this* finding has reached
 * (see {@link pipelineReach}). The final node borrows the verdict's colour once a
 * determination exists.
 *
 * Each *reached* or *current* circle is a plain {@link Link} to that stage's page:
 * clicking **navigates**, never mutates — the destructive moves (regenerate,
 * un-approve) are explicit buttons on the stage pages themselves. Not-yet-reached
 * stages stay inert. `activeStage` marks the page currently open.
 */
export function PipelineTrack({
  planned,
  approved,
  retested,
  verdict,
  findingId,
  activeStage,
}: {
  planned: boolean;
  approved: boolean;
  retested: boolean;
  verdict?: VerdictStatus;
  findingId: number;
  activeStage: Stage;
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
            const isActive = stage === activeStage;
            // A stage is walkable once it has been reached or is the next action.
            const navigable = isReached || isCurrent;
            let ring = "ring-line";
            let fill = "bg-line-2";
            if (isReached && i === 4 && verdict) {
              const tone = VERDICT_TONE[verdict];
              ring = TONE_RING[tone];
              fill = TONE_FILL[tone];
            } else if (isReached || isCurrent) {
              ring = TONE_RING.iris;
              fill = TONE_FILL.iris;
            }
            const node = (
              <>
                <span
                  className={`grid size-7 place-items-center rounded-full bg-panel-2 ring-1 ring-inset ${ring} ${isActive ? "ring-2" : ""} ${navigable ? "transition-transform group-hover:scale-110" : ""}`}
                >
                  <span
                    className={`size-2.5 rounded-full ${fill} ${isCurrent ? "rev-live" : ""}`}
                  />
                </span>
                <span
                  className={`font-mono text-[10px] uppercase tracking-[0.14em] ${isActive ? "text-fg" : isReached || isCurrent ? "text-dim" : "text-faint"} ${navigable ? "group-hover:text-fg" : ""}`}
                >
                  {stage}
                </span>
              </>
            );
            return (
              <li key={stage} className="flex flex-col items-center">
                {navigable ? (
                  <Link
                    to={`/findings/${String(findingId)}/${stage}`}
                    aria-current={isActive ? "step" : undefined}
                    aria-label={`Go to ${stage} stage`}
                    className="group flex cursor-pointer flex-col items-center gap-2 rounded-md focus-visible:ring-2 focus-visible:ring-iris/50 focus-visible:outline-none"
                  >
                    {node}
                  </Link>
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
