import type { Finding, Report, Severity, Verdict, VerdictStatus } from "../api/types";

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
 * Tally findings by severity — the overview's risk profile. Returns every
 * {@link Severity} key (worst→best is imposed by the reader, not here) so the
 * bar's shape is stable even when a band is empty.
 */
export function severityCounts(findings: Finding[]): Record<Severity, number> {
  const counts: Record<Severity, number> = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    info: 0,
  };
  for (const finding of findings) counts[finding.severity] += 1;
  return counts;
}

/**
 * The verdicts belonging to a given set of findings — the join the API leaves to
 * the client, since `/verdicts` is unscoped. Used wherever a meter must read one
 * report's slice of the append-only ledger rather than the whole workspace.
 */
export function verdictsFor(verdicts: Verdict[], findings: Finding[]): Verdict[] {
  const findingIds = new Set(findings.map((finding) => finding.id));
  return verdicts.filter((verdict) => findingIds.has(verdict.finding_id));
}

/**
 * Drop the findings whose report has been archived (#162). Archiving is a
 * reversible soft-hide (#128), so the overview meters must read the *active*
 * workspace — otherwise a report shelved to get it out of the way keeps
 * inflating the determination ledger and the risk profile.
 *
 * Filters against the *archived* reports rather than the active ones on
 * purpose: while that query is still in flight the list is empty and nothing is
 * dropped, so the bars settle downward instead of flashing empty. A finding with
 * no report (`report_id === null`) has nothing to archive and always survives.
 */
export function findingsNotArchived(findings: Finding[], archived: Report[]): Finding[] {
  const archivedIds = new Set(archived.map((report) => report.id));
  return findings.filter(
    (finding) => finding.report_id === null || !archivedIds.has(finding.report_id),
  );
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
