import { useState } from "react";

import type { Plan, PlannedAction, Probe } from "../api/types";
import {
  useApprovePlan,
  useEditPlan,
  useRejectPlan,
  useRetest,
} from "../hooks/usePlans";
import { errorMessage } from "../lib/format";
import { Eyebrow, Panel } from "./ui/Panel";
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

const inputClass =
  "mt-1 w-full rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-fg transition-colors placeholder:text-faint focus:border-iris/60 disabled:opacity-55";
const fieldLabel =
  "font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-faint";

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
    edit.isPending || approve.isPending || reject.isPending || runRetest.isPending;

  function updateField(index: number, field: keyof EditableAction, value: string) {
    setActions((current) =>
      current.map((action, i) =>
        i === index ? { ...action, [field]: value } : action,
      ),
    );
  }

  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-2">
          <Eyebrow>Retest plan</Eyebrow>
          <span className="font-mono text-sm font-semibold text-fg">v{plan.version}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] text-faint">origin: {plan.origin}</span>
          <StatusBadge status={plan.status} />
        </div>
      </div>

      <div className="p-4">
        {actions.length === 0 ? (
          <p className="text-sm text-dim">This plan has no runnable actions.</p>
        ) : (
          <ol className="space-y-3">
            {actions.map((action, index) => (
              <li
                key={index}
                className="grid grid-cols-1 gap-3 rounded-lg border border-line bg-panel-2/40 p-3 sm:grid-cols-[7rem_1fr]"
              >
                <div className="flex items-center gap-2 font-mono text-[11px] text-faint sm:col-span-2">
                  <span className="grid size-5 place-items-center rounded-md bg-iris/12 text-[10px] font-semibold text-iris-fg ring-1 ring-inset ring-iris/30">
                    {index + 1}
                  </span>
                  probe action
                </div>
                <label className={fieldLabel}>
                  Method
                  <input
                    aria-label={`Method for action ${String(index + 1)}`}
                    value={action.method}
                    disabled={!isProposed}
                    onChange={(event) => {
                      updateField(index, "method", event.target.value);
                    }}
                    className={`${inputClass} font-mono`}
                  />
                </label>
                <label className={fieldLabel}>
                  Target
                  <input
                    aria-label={`Target for action ${String(index + 1)}`}
                    value={action.target}
                    disabled={!isProposed}
                    onChange={(event) => {
                      updateField(index, "target", event.target.value);
                    }}
                    className={`${inputClass} font-mono`}
                  />
                </label>
                <label className={`${fieldLabel} sm:col-span-2`}>
                  Expected indicator
                  <input
                    aria-label={`Expected indicator for action ${String(index + 1)}`}
                    value={action.expected_indicator}
                    disabled={!isProposed}
                    onChange={(event) => {
                      updateField(index, "expected_indicator", event.target.value);
                    }}
                    className={inputClass}
                  />
                </label>
              </li>
            ))}
          </ol>
        )}

        {plan.rejected_actions.length > 0 && (
          <div className="mt-3 rounded-lg border border-danger/25 bg-danger/8 p-3">
            <div className="flex items-center gap-2">
              <svg
                width="13"
                height="13"
                viewBox="0 0 16 16"
                fill="none"
                aria-hidden="true"
                className="text-danger-fg"
              >
                <path
                  d="M8 1 1 14h14L8 1Z"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinejoin="round"
                />
                <path
                  d="M8 6v3.5"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
                <circle cx="8" cy="11.6" r="0.55" fill="currentColor" />
              </svg>
              <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-danger-fg">
                Dropped by the safety gate
              </h3>
            </div>
            <ul className="mt-2 space-y-1.5 text-[13px] text-dim">
              {plan.rejected_actions.map((rejected, index) => (
                <li key={index} className="flex flex-wrap gap-x-2">
                  <span className="font-mono text-fg">
                    {rejected.action.method} {rejected.action.target}
                  </span>
                  <span className="text-faint">— {rejected.reason}</span>
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
            className="rounded-lg border border-line px-3 py-1.5 font-mono text-[13px] font-medium text-dim transition-colors hover:bg-panel-2 hover:text-fg disabled:opacity-45"
          >
            Save edits
          </button>
          <button
            type="button"
            disabled={!isProposed || busy}
            onClick={() => {
              approve.mutate();
            }}
            className="rounded-lg bg-ok px-3 py-1.5 font-mono text-[13px] font-semibold text-onaccent transition-colors hover:brightness-110 disabled:opacity-45"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={!isProposed || busy}
            onClick={() => {
              reject.mutate();
            }}
            className="rounded-lg border border-danger/40 px-3 py-1.5 font-mono text-[13px] font-medium text-danger-fg transition-colors hover:bg-danger/10 disabled:opacity-45"
          >
            Reject
          </button>
          <button
            type="button"
            disabled={!isApproved || busy}
            onClick={() => {
              runRetest.mutate();
            }}
            className="ml-auto inline-flex items-center gap-2 rounded-lg bg-iris px-3.5 py-1.5 font-mono text-[13px] font-semibold text-onaccent transition-colors hover:bg-iris-bright disabled:opacity-45"
          >
            {runRetest.isPending ? "Running retest…" : "Run retest"}
          </button>
        </div>

        {(edit.isError || approve.isError || reject.isError || runRetest.isError) && (
          <p role="alert" className="mt-3 text-sm text-danger-fg">
            {errorMessage(edit.error ?? approve.error ?? reject.error ?? runRetest.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}
