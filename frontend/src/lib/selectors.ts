import type { Plan, Verdict, VerdictStatus } from "../api/types";

/** Statuses the detail view still surfaces (superseded/rejected are history). */
const LIVE_PLAN_STATUSES = new Set<Plan["status"]>([
  "generating",
  "proposed",
  "approved",
  "failed",
]);

/**
 * The plan the detail view acts on: the newest version that is still live — in
 * flight (`generating`), awaiting a decision (`proposed`), decided (`approved`),
 * or `failed` (shown so the user can see why and retry). Superseded and rejected
 * versions are history and never surface here.
 */
export function currentPlan(plans: Plan[]): Plan | undefined {
  return plans
    .filter((plan) => LIVE_PLAN_STATUSES.has(plan.status))
    .reduce<Plan | undefined>(
      (latest, plan) => (!latest || plan.version > latest.version ? plan : latest),
      undefined,
    );
}

/** Latest verdict for a finding = highest id among that finding's verdicts. */
export function latestVerdict(verdicts: Verdict[], findingId: number): Verdict | undefined {
  return verdicts
    .filter((verdict) => verdict.finding_id === findingId)
    .reduce<Verdict | undefined>(
      (latest, verdict) => (!latest || verdict.id > latest.id ? verdict : latest),
      undefined,
    );
}

/** Tally verdicts by status for the determination meter. */
export function verdictCounts(verdicts: Verdict[]): Record<VerdictStatus, number> {
  const counts: Record<VerdictStatus, number> = {
    still_open: 0,
    inconclusive: 0,
    fixed: 0,
  };
  for (const verdict of verdicts) {
    counts[verdict.status] += 1;
  }
  return counts;
}

/**
 * How far a finding has advanced along the fixed pipeline
 * (extract → plan → approve → retest → verdict). Cumulative and monotonic: a
 * later state implies the earlier ones happened.
 *
 * Returns the per-stage `reached` flags plus two indices with distinct jobs:
 * `furthest` is the last *completed* stage (drives the progress fill), while
 * `current` is the stage the finding is *acting on now* — the first not-yet-
 * reached stage, i.e. the next thing to do. So on the generate-plan screen
 * (nothing done yet) `current` is `plan`, not `extract`. Once every stage is
 * reached the two coincide on the final `verdict` node.
 */
export function pipelineReach({
  planned,
  approved,
  retested,
}: {
  planned: boolean;
  approved: boolean;
  retested: boolean;
}): { reached: boolean[]; furthest: number; current: number } {
  const reached = [
    true,
    planned || approved || retested,
    approved || retested,
    retested,
    retested,
  ];
  const nextUnreached = reached.indexOf(false);
  return {
    reached,
    furthest: reached.lastIndexOf(true),
    current: nextUnreached === -1 ? reached.length - 1 : nextUnreached,
  };
}
