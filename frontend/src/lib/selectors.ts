import type { Verdict, VerdictStatus } from "../api/types";

/** Latest verdict for a finding = highest id among that finding's verdicts. */
export function latestVerdict(verdicts: Verdict[], findingId: number): Verdict | undefined {
  return verdicts
    .filter((verdict) => verdict.finding_id === findingId)
    .reduce<Verdict | undefined>(
      (latest, verdict) => (!latest || verdict.id > latest.id ? verdict : latest),
      undefined,
    );
}

/**
 * Tally the determination meter by counting **one verdict per finding** — the
 * latest (highest id) for each `finding_id`. Verdicts are append-only for the
 * audit trail (a re-run or an operator adjudication supersedes rather than
 * replaces), so counting every row would inflate the ledger; a finding
 * contributes exactly one determination — its current one.
 */
export function verdictCounts(verdicts: Verdict[]): Record<VerdictStatus, number> {
  const counts: Record<VerdictStatus, number> = {
    still_open: 0,
    inconclusive: 0,
    fixed: 0,
  };
  const latestByFinding = new Map<number, Verdict>();
  for (const verdict of verdicts) {
    const current = latestByFinding.get(verdict.finding_id);
    if (!current || verdict.id > current.id) {
      latestByFinding.set(verdict.finding_id, verdict);
    }
  }
  for (const verdict of latestByFinding.values()) {
    counts[verdict.status] += 1;
  }
  return counts;
}

/**
 * The four-stage revalidation pipeline as reach flags (FR-17 6b-iii-b):
 * extract → goal → retest → verdict. Extract and goal are always reachable;
 * retest opens once a session exists; verdict once a verdict exists.
 */
export function pipelineReach({
  sessionExists,
  hasVerdict,
}: {
  sessionExists: boolean;
  hasVerdict: boolean;
}): { reached: boolean[]; furthest: number; current: number } {
  const reached = [true, true, sessionExists || hasVerdict, hasVerdict];
  const nextUnreached = reached.indexOf(false);
  return {
    reached,
    furthest: reached.lastIndexOf(true),
    current: nextUnreached === -1 ? reached.length - 1 : nextUnreached,
  };
}
