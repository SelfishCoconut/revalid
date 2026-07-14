import type { Plan } from "../api/types";
import { formatDateTime } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

/** Audit view: every plan version with its status and decision metadata. */
export function PlanHistory({ plans }: { plans: Plan[] }) {
  if (plans.length === 0) {
    return <p className="px-4 py-3 text-sm text-faint">No plans generated yet.</p>;
  }

  const ordered = [...plans].sort((a, b) => b.version - a.version);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-left">
        <thead>
          <tr className="border-b border-line font-mono text-[10px] uppercase tracking-[0.16em] text-faint">
            <th className="px-4 py-2.5 font-medium">Version</th>
            <th className="px-4 py-2.5 font-medium">Status</th>
            <th className="px-4 py-2.5 font-medium">Origin</th>
            <th className="px-4 py-2.5 font-medium">Decided at</th>
            <th className="px-4 py-2.5 font-medium">Decided by</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line/60">
          {ordered.map((plan) => (
            <tr key={plan.id} className="transition-colors hover:bg-panel-2/40">
              <td className="px-4 py-2.5 font-mono text-sm font-medium text-fg">
                v{plan.version}
              </td>
              <td className="px-4 py-2.5">
                <StatusBadge status={plan.status} />
              </td>
              <td className="px-4 py-2.5 font-mono text-[13px] text-dim">{plan.origin}</td>
              <td className="px-4 py-2.5 font-mono text-[13px] text-dim">
                {plan.decided_at ? formatDateTime(plan.decided_at) : "—"}
              </td>
              <td className="px-4 py-2.5 font-mono text-[13px] text-dim">
                {plan.decided_by ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
