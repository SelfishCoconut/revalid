import type { Plan, Verdict, VerdictStatus } from "../api/types";

/** The plan the workflow acts on: newest version that is proposed or approved. */
export function activePlan(plans: Plan[]): Plan | undefined {
  return plans
    .filter((plan) => plan.status === "proposed" || plan.status === "approved")
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
 * later state implies the earlier ones happened. Returns the per-stage reached
 * flags and the index of the furthest reached stage.
 */
export function pipelineReach({
  planned,
  approved,
  retested,
}: {
  planned: boolean;
  approved: boolean;
  retested: boolean;
}): { reached: boolean[]; current: number } {
  const reached = [
    true,
    planned || approved || retested,
    approved || retested,
    retested,
    retested,
  ];
  return { reached, current: reached.lastIndexOf(true) };
}
