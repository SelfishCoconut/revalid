import type { Plan } from "../api/types";
import { formatDateTime } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

/** Audit view: every plan version with its status and decision metadata. */
export function PlanHistory({ plans }: { plans: Plan[] }) {
  if (plans.length === 0) {
    return <p className="text-sm text-slate-500">No plans generated yet.</p>;
  }

  const ordered = [...plans].sort((a, b) => b.version - a.version);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-left text-sm">
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-2 pr-4">Version</th>
            <th className="py-2 pr-4">Status</th>
            <th className="py-2 pr-4">Origin</th>
            <th className="py-2 pr-4">Decided at</th>
            <th className="py-2">Decided by</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {ordered.map((plan) => (
            <tr key={plan.id}>
              <td className="py-2 pr-4 font-medium text-slate-800">v{plan.version}</td>
              <td className="py-2 pr-4">
                <StatusBadge status={plan.status} />
              </td>
              <td className="py-2 pr-4 text-slate-600">{plan.origin}</td>
              <td className="py-2 pr-4 text-slate-600">
                {plan.decided_at ? formatDateTime(plan.decided_at) : "—"}
              </td>
              <td className="py-2 text-slate-600">{plan.decided_by ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
