import { Link } from "react-router-dom";

import { DeterminationMeter } from "../../components/DeterminationMeter";
import { useFindingStage } from "../../hooks/useFindingStage";
import { NotesThread } from "../../components/NotesThread";
import { VerdictCard } from "../../components/VerdictCard";
import { Panel, PanelHeader } from "../../components/ui/Panel";
import { verdictCounts } from "../../lib/selectors";

/** Stage 5 — the recorded determination and its evidence-backed verdicts (FR-09). */
export function VerdictStage() {
  const { findingId, verdicts } = useFindingStage();

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader
          eyebrow="Verdict"
          aside={
            <span className="font-mono text-[11px] text-faint">{verdicts.length} recorded</span>
          }
        />
        <div className="space-y-4 p-4">
          {verdicts.length === 0 ? (
            <p className="text-sm text-faint">
              No verdicts yet.{" "}
              <Link
                to={`/findings/${String(findingId)}/retest`}
                className="text-iris-fg hover:underline"
              >
                Run the retest
              </Link>{" "}
              once a plan is approved.
            </p>
          ) : (
            <>
              <DeterminationMeter counts={verdictCounts(verdicts)} />
              <div className="space-y-3">
                {verdicts.map((verdict) => (
                  <VerdictCard key={verdict.id} verdict={verdict} />
                ))}
              </div>
            </>
          )}
        </div>
      </Panel>
      <NotesThread findingId={findingId} stage="verdict" scope="stage" />
    </div>
  );
}
