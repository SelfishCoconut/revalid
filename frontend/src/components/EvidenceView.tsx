import type { AgenticEvidence, Verdict } from "../api/types";

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-0.5 sm:grid-cols-[9rem_1fr] sm:gap-3">
      <dt className="font-mono text-[11px] uppercase tracking-[0.14em] text-faint">
        {label}
      </dt>
      <dd className={`text-[13px] text-fg ${mono ? "break-all font-mono text-dim" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

/**
 * Expandable command-output evidence captured for a verdict: the agent's
 * explanation, the command it ran, what came back, and how long it took.
 */
function AgenticEvidenceView({ evidence }: { evidence: AgenticEvidence }) {
  return (
    <details className="group mt-3 overflow-hidden rounded-lg border border-line bg-panel-2/50">
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-dim transition-colors hover:text-fg">
        Evidence
      </summary>
      <dl className="space-y-2.5 border-t border-line px-3 py-3">
        <Field label="Explanation" value={evidence.explanation} />
        {evidence.command && <Field label="Command" value={evidence.command} mono />}
        {evidence.output && <Field label="Output" value={evidence.output} mono />}
        {evidence.exit_code !== null && (
          <Field label="Exit code" value={String(evidence.exit_code)} />
        )}
        <Field label="Elapsed" value={`${String(evidence.elapsed_ms)} ms`} />
      </dl>
    </details>
  );
}

export function EvidenceView({ verdict }: { verdict: Verdict }) {
  const { evidence } = verdict;
  if (evidence === null) {
    return null;
  }
  return <AgenticEvidenceView evidence={evidence} />;
}
