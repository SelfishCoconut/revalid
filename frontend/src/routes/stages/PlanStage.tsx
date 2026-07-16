import { useState } from "react";

import { Link } from "react-router-dom";

import type { Plan } from "../../api/types";
import { useFindingStage } from "../../hooks/useFindingStage";
import { InstructionsField } from "../../components/InstructionsField";
import { NotesThread } from "../../components/NotesThread";
import { PlanActions } from "../../components/PlanActions";
import { Spinner } from "../../components/Spinner";
import { StatusBadge } from "../../components/StatusBadge";
import { Button } from "../../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../../components/ui/Panel";
import { useEditPlan, useGeneratePlan } from "../../hooks/usePlans";
import { errorMessage } from "../../lib/format";
import { type EditableAction, toEditable, toPlannedAction } from "../../lib/planActions";

/** No live plan yet (or the last attempt failed): kick off async generation. */
function PlanStart({ findingId, failedError }: { findingId: number; failedError: string | null }) {
  const generate = useGeneratePlan(findingId);
  const [instructions, setInstructions] = useState("");
  return (
    <Panel>
      <PanelHeader eyebrow="Retest plan" />
      <div className="space-y-3 p-4">
        {failedError !== null ? (
          <p className="text-sm text-danger-fg">Plan generation failed: {failedError}</p>
        ) : (
          <p className="text-sm text-dim">
            No plan yet. Generate one to propose gated retest actions.
          </p>
        )}
        <InstructionsField
          value={instructions}
          onChange={setInstructions}
          disabled={generate.isPending}
        />
        <Button
          disabled={generate.isPending}
          onClick={() => {
            generate.mutate(instructions);
          }}
        >
          {generate.isPending ? "Generating…" : failedError !== null ? "Try again" : "Generate plan"}
        </Button>
        {generate.isError && (
          <p role="alert" className="text-sm text-danger-fg">
            {errorMessage(generate.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}

/** In-flight generation — the background task is proposing actions (ADR-0022). */
function PlanGenerating() {
  return (
    <Panel>
      <PanelHeader eyebrow="Retest plan" />
      <div className="p-4">
        <Spinner label="Generating retest plan…" />
        <p className="mt-2 text-sm text-faint">
          The model is proposing gated retest actions. This runs in the background — you can leave
          this page and the plan will be here when you return.
        </p>
      </div>
    </Panel>
  );
}

/**
 * The proposed/approved plan: edit its gated actions (proposed only), or discard
 * and regenerate with fresh guidance. Approve/reject lives on the Approve stage.
 * Keyed by the plan id so the editable state re-seeds when a new version lands.
 */
function PlanEditorPanel({ findingId, plan }: { findingId: number; plan: Plan }) {
  const [actions, setActions] = useState<EditableAction[]>(() => plan.actions.map(toEditable));
  const [regenInstructions, setRegenInstructions] = useState("");
  const edit = useEditPlan(findingId);
  const regenerate = useGeneratePlan(findingId);
  const isProposed = plan.status === "proposed";
  const busy = edit.isPending || regenerate.isPending;

  function updateField(index: number, field: keyof EditableAction, value: string) {
    setActions((current) =>
      current.map((action, i) => (i === index ? { ...action, [field]: value } : action)),
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

      <div className="space-y-4 p-4">
        <PlanActions
          plan={plan}
          actions={actions}
          editable={isProposed}
          onFieldChange={updateField}
        />

        {isProposed ? (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              edit.mutate(actions.map(toPlannedAction));
            }}
          >
            {edit.isPending ? "Saving…" : "Save edits"}
          </Button>
        ) : (
          <p className="text-sm text-dim">
            This plan is approved. Un-approve it on the{" "}
            <Link
              to={`/findings/${String(findingId)}/approve`}
              className="text-iris-fg hover:underline"
            >
              Approve stage
            </Link>{" "}
            to edit its actions, or discard and regenerate below.
          </p>
        )}

        {/* Go back a step: throw this plan away and generate anew (ADR-0023). */}
        <div className="space-y-2 border-t border-line pt-4">
          <InstructionsField
            id="regen-instructions"
            value={regenInstructions}
            onChange={setRegenInstructions}
            disabled={busy}
          />
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              regenerate.mutate(regenInstructions);
            }}
          >
            {regenerate.isPending ? "Regenerating…" : "Discard & regenerate"}
          </Button>
        </div>

        {(edit.isError || regenerate.isError) && (
          <p role="alert" className="text-sm text-danger-fg">
            {errorMessage(edit.error ?? regenerate.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}

/** Stage 2 — generate, edit, or regenerate the retest plan (FR-04/FR-05). */
export function PlanStage() {
  const { findingId, currentPlan } = useFindingStage();

  let panel;
  if (currentPlan?.status === "generating") {
    panel = <PlanGenerating />;
  } else if (currentPlan && (currentPlan.status === "proposed" || currentPlan.status === "approved")) {
    panel = <PlanEditorPanel key={currentPlan.id} findingId={findingId} plan={currentPlan} />;
  } else {
    panel = (
      <PlanStart
        findingId={findingId}
        failedError={currentPlan?.status === "failed" ? currentPlan.error : null}
      />
    );
  }

  return (
    <div className="space-y-6">
      {panel}
      <NotesThread findingId={findingId} stage="plan" scope="stage" />
    </div>
  );
}
