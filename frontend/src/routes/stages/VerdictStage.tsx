import { Link } from "react-router-dom";

import { useFindingStage } from "../../hooks/useFindingStage";
import { NotesThread } from "../../components/NotesThread";
import { VerdictCard } from "../../components/VerdictCard";
import { Panel, PanelHeader } from "../../components/ui/Panel";

/** Stage 5 — the recorded determination and its evidence-backed verdicts (FR-09). */
export function VerdictStage() {
  const { findingId, verdicts } = useFindingStage();
  // Verdicts arrive newest-first: the head is the current determination; the
  // tail is superseded history — kept for the audit trail but tucked behind an
  // expander so the page shows the up-to-date verdict by default (issue #130).
  const [latest, ...history] = verdicts;

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
              to record a determination.
            </p>
          ) : (
            <>
              <VerdictCard verdict={latest} />
              {history.length > 0 && (
                <details className="group rounded-lg border border-line bg-panel-2/40">
                  <summary className="cursor-pointer list-none px-4 py-2.5 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-faint transition-colors hover:text-dim">
                    <span className="inline-flex items-center gap-2">
                      <span
                        aria-hidden="true"
                        className="transition-transform group-open:rotate-90"
                      >
                        ▸
                      </span>
                      History — {history.length} superseded
                    </span>
                  </summary>
                  <div className="space-y-3 border-t border-line p-4">
                    {history.map((verdict) => (
                      <VerdictCard key={verdict.id} verdict={verdict} />
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      </Panel>
      <NotesThread findingId={findingId} stage="verdict" scope="stage" />
    </div>
  );
}
