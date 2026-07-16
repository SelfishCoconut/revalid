import type { Plan } from "../api/types";
import type { EditableAction } from "../lib/planActions";

const inputClass =
  "mt-1 w-full rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-fg transition-colors placeholder:text-faint focus:border-iris/60 disabled:opacity-55";
const fieldLabel = "font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-faint";

/** Read-only value shown where an input would be when the plan isn't editable. */
function ReadValue({ children }: { children: string }) {
  return <div className="mt-1 font-mono text-[13px] break-all text-dim">{children || "—"}</div>;
}

/**
 * Renders a plan's gated probe actions — editable on a proposed plan, read-only
 * otherwise — plus the applied operator guidance and anything the FR-06 gate
 * dropped. Buttons live on the stage pages; this is just the body they share.
 */
export function PlanActions({
  plan,
  actions,
  editable,
  onFieldChange,
}: {
  plan: Plan;
  actions: EditableAction[];
  editable: boolean;
  onFieldChange?: (index: number, field: keyof EditableAction, value: string) => void;
}) {
  const appliedInstructions =
    typeof plan.raw.instructions === "string" ? plan.raw.instructions : "";

  return (
    <div>
      {appliedInstructions && (
        <div className="mb-3 rounded-lg border border-iris/25 bg-iris/8 px-3 py-2">
          <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-iris-fg">
            Guidance applied
          </span>
          <p className="mt-1 text-[13px] text-dim">{appliedInstructions}</p>
        </div>
      )}

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
                {editable ? (
                  <input
                    aria-label={`Method for action ${String(index + 1)}`}
                    value={action.method}
                    onChange={(event) => onFieldChange?.(index, "method", event.target.value)}
                    className={`${inputClass} font-mono`}
                  />
                ) : (
                  <ReadValue>{action.method}</ReadValue>
                )}
              </label>
              <label className={fieldLabel}>
                Target
                {editable ? (
                  <input
                    aria-label={`Target for action ${String(index + 1)}`}
                    value={action.target}
                    onChange={(event) => onFieldChange?.(index, "target", event.target.value)}
                    className={`${inputClass} font-mono`}
                  />
                ) : (
                  <ReadValue>{action.target}</ReadValue>
                )}
              </label>
              <label className={`${fieldLabel} sm:col-span-2`}>
                Expected indicator
                {editable ? (
                  <input
                    aria-label={`Expected indicator for action ${String(index + 1)}`}
                    value={action.expected_indicator}
                    onChange={(event) =>
                      onFieldChange?.(index, "expected_indicator", event.target.value)
                    }
                    className={inputClass}
                  />
                ) : (
                  <ReadValue>{action.expected_indicator}</ReadValue>
                )}
              </label>
            </li>
          ))}
        </ol>
      )}

      {plan.rejected_actions.length > 0 && (
        <div className="mt-3 rounded-lg border border-danger/25 bg-danger/8 p-3">
          <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-danger-fg">
            Dropped by the safety gate
          </h3>
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
    </div>
  );
}
