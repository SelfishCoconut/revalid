import type { Verdict } from "../api/types";

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-1 gap-0.5 sm:grid-cols-[10rem_1fr]">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`text-sm text-slate-800 ${mono ? "font-mono break-all" : ""}`}>{value}</dd>
    </div>
  );
}

/**
 * Expandable request/response evidence captured for a verdict: what was sent,
 * what came back, timing, and which indicators matched.
 */
export function EvidenceView({ verdict }: { verdict: Verdict }) {
  const { evidence } = verdict;
  return (
    <details className="mt-2 rounded-md border border-slate-200 bg-slate-50">
      <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-slate-700">
        Evidence
      </summary>
      <dl className="space-y-2 px-3 pb-3">
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
