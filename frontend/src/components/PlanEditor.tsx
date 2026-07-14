import { useState } from "react";

import type { Plan, PlannedAction, Probe } from "../api/types";
import {
  useApprovePlan,
  useEditPlan,
  useRejectPlan,
  useRetest,
} from "../hooks/usePlans";
import { errorMessage } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

/** Editable form row derived from a probe; carries non-edited fields through. */
interface EditableAction {
  method: string;
  target: string;
  expected_indicator: string;
  headers: Record<string, string>;
  json_body: Record<string, unknown> | null;
}

function toEditable(probe: Probe): EditableAction {
  return {
    method: probe.method,
    target: probe.url,
    expected_indicator: probe.expected_indicator,
    headers: probe.headers,
    json_body: probe.json_body,
  };
}

function toPlannedAction(action: EditableAction): PlannedAction {
  return {
    method: action.method,
    target: action.target,
    headers: action.headers,
    json_body: action.json_body,
    expected_indicator: action.expected_indicator,
  };
}

/**
 * Plan workflow surface (FR-05): edit the proposed actions, approve/reject the
 * plan, and run the retest once approved. Mutations invalidate the plan/verdict
 * queries so the rest of the page refreshes on its own.
 */
export function PlanEditor({ findingId, plan }: { findingId: number; plan: Plan }) {
  const [actions, setActions] = useState<EditableAction[]>(() =>
    plan.actions.map(toEditable),
  );

  const edit = useEditPlan(findingId);
  const approve = useApprovePlan(findingId);
  const reject = useRejectPlan(findingId);
  const runRetest = useRetest(findingId);

  const isProposed = plan.status === "proposed";
  const isApproved = plan.status === "approved";
  const busy =
    edit.isPending ||
    approve.isPending ||
    reject.isPending ||
    runRetest.isPending;

  function updateField(index: number, field: keyof EditableAction, value: string) {
    setActions((current) =>
      current.map((action, i) =>
        i === index ? { ...action, [field]: value } : action,
      ),
    );
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold text-slate-800">
          Retest plan{" "}
          <span className="text-slate-400">v{plan.version}</span>
        </h2>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span>origin: {plan.origin}</span>
          <StatusBadge status={plan.status} />
        </div>
      </header>

      {actions.length === 0 ? (
        <p className="text-sm text-slate-500">This plan has no runnable actions.</p>
      ) : (
        <ol className="space-y-3">
          {actions.map((action, index) => (
            <li
              key={index}
              className="grid grid-cols-1 gap-2 rounded-md border border-slate-100 bg-slate-50 p-3 sm:grid-cols-[6rem_1fr]"
            >
              <label className="text-xs font-medium text-slate-500">
                Method
                <input
                  aria-label={`Method for action ${String(index + 1)}`}
                  value={action.method}
                  disabled={!isProposed}
                  onChange={(event) => {
                    updateField(index, "method", event.target.value);
                  }}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 disabled:bg-slate-100"
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                Target
                <input
                  aria-label={`Target for action ${String(index + 1)}`}
                  value={action.target}
                  disabled={!isProposed}
                  onChange={(event) => {
                    updateField(index, "target", event.target.value);
                  }}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-sm text-slate-900 disabled:bg-slate-100"
                />
              </label>
              <label className="text-xs font-medium text-slate-500 sm:col-span-2">
                Expected indicator
                <input
                  aria-label={`Expected indicator for action ${String(index + 1)}`}
                  value={action.expected_indicator}
                  disabled={!isProposed}
                  onChange={(event) => {
                    updateField(index, "expected_indicator", event.target.value);
                  }}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 disabled:bg-slate-100"
                />
              </label>
            </li>
          ))}
        </ol>
      )}

      {plan.rejected_actions.length > 0 && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-800">
            Dropped by the safety gate
          </h3>
          <ul className="mt-2 space-y-1 text-sm text-amber-900">
            {plan.rejected_actions.map((rejected, index) => (
              <li key={index}>
                <span className="font-mono">
                  {rejected.action.method} {rejected.action.target}
                </span>{" "}
                — {rejected.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={!isProposed || busy}
          onClick={() => {
            edit.mutate(actions.map(toPlannedAction));
          }}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          Save edits
        </button>
        <button
          type="button"
          disabled={!isProposed || busy}
          onClick={() => {
            approve.mutate();
          }}
          className="rounded-md bg-green-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-600 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={!isProposed || busy}
          onClick={() => {
            reject.mutate();
          }}
          className="rounded-md bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50"
        >
          Reject
        </button>
        <button
          type="button"
          disabled={!isApproved || busy}
          onClick={() => {
            runRetest.mutate();
          }}
          className="ml-auto rounded-md bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {runRetest.isPending ? "Running retest…" : "Run retest"}
        </button>
      </div>

      {(edit.isError || approve.isError || reject.isError || runRetest.isError) && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {errorMessage(
            edit.error ?? approve.error ?? reject.error ?? runRetest.error,
          )}
        </p>
      )}
    </section>
  );
}
