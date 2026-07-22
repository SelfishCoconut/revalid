import type { CvssCode, MitreMapping } from "../api/types";

/**
 * A finding's classificatory metadata: CVSS severity code and MITRE ATT&CK
 * techniques (FR-19, ADR-0037).
 *
 * Two rules this component exists to hold:
 *
 * 1. **Derived values are visibly marked.** A value the report stated and one the
 *    model inferred look nothing alike here, because treating an estimate as a
 *    quoted fact is exactly the failure a revalidation tool cannot afford.
 * 2. **Absent is absent.** No CVSS code means an em dash, never `0.0` — a
 *    fabricated zero would read as "harmless" for something simply unscored.
 *
 * These fields are metadata only: they never feed a retest verdict (ADR-0037).
 */
export function FindingTaxonomy({ cvss, mitre }: { cvss: CvssCode; mitre: MitreMapping }) {
  const hasCvss = cvss.vector !== "" || cvss.base_score !== null;
  const hasMitre = mitre.techniques.length > 0;

  return (
    <dl className="grid gap-3 sm:grid-cols-2" aria-label="Finding taxonomy">
      <div className="min-w-0">
        <dt className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.12em] text-faint">
          CVSS
          {hasCvss && cvss.inferred && <InferredBadge />}
        </dt>
        <dd className="mt-1.5 min-w-0">
          {hasCvss ? (
            <div className="space-y-1">
              {cvss.base_score !== null && (
                <span className="font-mono text-sm font-semibold text-fg">
                  {cvss.base_score.toFixed(1)}
                </span>
              )}
              {cvss.vector !== "" && (
                <p className="break-all font-mono text-[11px] text-dim">{cvss.vector}</p>
              )}
            </div>
          ) : (
            <Absent what="No CVSS code stated or derived" />
          )}
        </dd>
      </div>

      <div className="min-w-0">
        <dt className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.12em] text-faint">
          MITRE ATT&amp;CK
          {hasMitre && mitre.inferred && <InferredBadge />}
        </dt>
        <dd className="mt-1.5">
          {hasMitre ? (
            <ul className="flex flex-wrap gap-1.5">
              {mitre.techniques.map((technique) => (
                <li
                  key={technique}
                  className="rounded border border-line-2 px-1.5 py-0.5 font-mono text-[11px] text-fg"
                >
                  {technique}
                </li>
              ))}
            </ul>
          ) : (
            <Absent what="No ATT&CK technique stated or derived" />
          )}
        </dd>
      </div>
    </dl>
  );
}

/** Marks a value the model derived rather than read from the report (FR-19). */
function InferredBadge() {
  return (
    <span
      title="Derived by the model — the report stated none"
      className="rounded-full bg-warn/15 px-1.5 py-0.5 font-mono text-[10px] font-medium normal-case tracking-normal text-warn-fg ring-1 ring-inset ring-warn/30"
    >
      inferred
    </span>
  );
}

/** An honest em dash for a value nobody stated and nobody derived. */
function Absent({ what }: { what: string }) {
  return (
    <span className="font-mono text-sm text-faint" title={what}>
      —
    </span>
  );
}
