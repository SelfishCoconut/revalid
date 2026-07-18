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
 * Expandable request/response evidence captured for a verdict: what was sent,
 * what came back, timing, and which indicators matched.
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
  // An agentic verdict (FR-17) carries flexible command-output proof, not an
  // HTTP request/response — render its explanation + command + output.
  if ("explanation" in evidence) {
    return <AgenticEvidenceView evidence={evidence} />;
  }
  return (
    <details className="group mt-3 overflow-hidden rounded-lg border border-line bg-panel-2/50">
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-dim transition-colors hover:text-fg">
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          fill="none"
          aria-hidden="true"
          className="transition-transform group-open:rotate-90"
        >
          <path
            d="m3 1 4 4-4 4"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Evidence
      </summary>
      <dl className="space-y-2.5 border-t border-line px-3 py-3">
        <Field
          label="Request"
          value={`${evidence.request_method} ${evidence.request_url}`}
          mono
        />
        {evidence.request_body && (
          <Field label="Request body" value={evidence.request_body} mono />
        )}
        <Field label="Response status" value={String(evidence.response_status)} />
        <Field label="Elapsed" value={`${String(evidence.elapsed_ms)} ms`} />
        <Field label="Response body" value={evidence.response_body_excerpt} mono />
        <Field
          label="Matched indicators"
          value={
            verdict.matched_indicators.length > 0
              ? verdict.matched_indicators.join(", ")
              : "none"
          }
        />
      </dl>
    </details>
  );
}
