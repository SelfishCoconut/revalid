import { Link } from "react-router-dom";

import { useFindingStage } from "../../hooks/useFindingStage";
import { NotesThread } from "../../components/NotesThread";
import { PlanActions } from "../../components/PlanActions";
import { StatusBadge } from "../../components/StatusBadge";
import { Button } from "../../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../../components/ui/Panel";
import { useApprovePlan, useRejectPlan, useRevisePlan } from "../../hooks/usePlans";
import { errorMessage } from "../../lib/format";
import { toEditable } from "../../lib/planActions";

/** Stage 3 — review the proposed plan and approve/reject it, or un-approve (FR-05). */
export function ApproveStage() {
  const { findingId, currentPlan } = useFindingStage();
  const approve = useApprovePlan(findingId);
  const reject = useRejectPlan(findingId);
  const revise = useRevisePlan(findingId);
  const busy = approve.isPending || reject.isPending || revise.isPending;

  const notes = <NotesThread findingId={findingId} stage="approve" scope="stage" />;

  if (!currentPlan || currentPlan.status === "generating" || currentPlan.status === "failed") {
    return (
      <div className="space-y-6">
        <Panel>
          <PanelHeader eyebrow="Approve plan" />
          <p className="p-4 text-sm text-dim">
            No plan to approve yet.{" "}
            <Link
              to={`/findings/${String(findingId)}/plan`}
              className="text-iris-fg hover:underline"
            >
              Generate a plan
            </Link>{" "}
            first.
          </p>
        </Panel>
        {notes}
      </div>
    );
  }

  const isProposed = currentPlan.status === "proposed";
  const isApproved = currentPlan.status === "approved";
  const actions = currentPlan.actions.map(toEditable);

  return (
    <div className="space-y-6">
      <Panel>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex items-baseline gap-2">
            <Eyebrow>Approve plan</Eyebrow>
            <span className="font-mono text-sm font-semibold text-fg">v{currentPlan.version}</span>
          </div>
          <StatusBadge status={currentPlan.status} />
        </div>
        <div className="space-y-4 p-4">
          <PlanActions plan={currentPlan} actions={actions} editable={false} />

          <div className="flex flex-wrap items-center gap-2">
            {isProposed && (
              <>
                <Button
                  variant="positive"
                  disabled={busy}
                  onClick={() => {
                    approve.mutate();
                  }}
                >
                  Approve
                </Button>
                <Button
                  variant="danger"
                  disabled={busy}
                  onClick={() => {
                    reject.mutate();
                  }}
                >
                  Reject
                </Button>
                <Link
                  to={`/findings/${String(findingId)}/plan`}
                  className="ml-auto font-mono text-[12px] text-faint hover:text-dim"
                >
                  ← edit on plan stage
                </Link>
              </>
            )}
            {isApproved && (
              <>
                <span className="text-sm text-ok-fg">Approved — ready to retest.</span>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    revise.mutate();
                  }}
                >
                  Un-approve / revise
                </Button>
                <Link
                  to={`/findings/${String(findingId)}/retest`}
                  className="ml-auto font-mono text-[12px] text-iris-fg hover:underline"
                >
                  Run retest →
                </Link>
              </>
            )}
          </div>

          {(approve.isError || reject.isError || revise.isError) && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(approve.error ?? reject.error ?? revise.error)}
            </p>
          )}
        </div>
      </Panel>
      {notes}
    </div>
  );
}
