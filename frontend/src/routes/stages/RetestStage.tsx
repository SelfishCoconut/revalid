import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { useFindingStage } from "../../hooks/useFindingStage";
import { NotesThread } from "../../components/NotesThread";
import { Button } from "../../components/ui/Button";
import { Panel, PanelHeader } from "../../components/ui/Panel";
import { startRetestSession } from "../../api/client";
import { useRetest } from "../../hooks/usePlans";
import { errorMessage } from "../../lib/format";

/** Stage 4 — execute the approved plan and record verdicts (FR-05/FR-07). */
export function RetestStage() {
  const { findingId, approved, verdicts } = useFindingStage();
  const runRetest = useRetest(findingId);
  const navigate = useNavigate();
  // The agentic session (FR-17) is a separate, additive entry point — the
  // batch "Run retest" flow above stays the primary path and keeps working
  // unchanged.
  const startSession = useMutation({
    mutationFn: () => startRetestSession(findingId),
    onSuccess: (session) => {
      navigate(`/retest-sessions/${String(session.id)}`);
    },
  });

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader eyebrow="Retest" />
        <div className="space-y-3 p-4">
          {!approved ? (
            <p className="text-sm text-dim">
              Nothing runs until a plan is approved.{" "}
              <Link
                to={`/findings/${String(findingId)}/approve`}
                className="text-iris-fg hover:underline"
              >
                Approve a plan
              </Link>{" "}
              first.
            </p>
          ) : (
            <>
              <p className="text-sm text-dim">
                Execute the approved plan's gated probes against the allowlisted lab target and
                record evidence-backed verdicts.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  disabled={runRetest.isPending}
                  onClick={() => {
                    runRetest.mutate();
                  }}
                >
                  {runRetest.isPending
                    ? "Running retest…"
                    : verdicts.length > 0
                      ? "Run retest again"
                      : "Run retest"}
                </Button>
                {(runRetest.isSuccess || verdicts.length > 0) && (
                  <Link
                    to={`/findings/${String(findingId)}/verdict`}
                    className="font-mono text-[12px] text-iris-fg hover:underline"
                  >
                    View verdict →
                  </Link>
                )}
              </div>
              {runRetest.isError && (
                <p role="alert" className="text-sm text-danger-fg">
                  {errorMessage(runRetest.error)}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-3 border-t border-line pt-3">
                <Button
                  variant="ghost"
                  disabled={startSession.isPending}
                  onClick={() => {
                    startSession.mutate();
                  }}
                >
                  {startSession.isPending
                    ? "Starting session…"
                    : "Start agentic retest session"}
                </Button>
                <p className="font-mono text-[11px] text-faint">
                  Watch the agent propose and run commands live, with a gate on each one.
                </p>
              </div>
              {startSession.isError && (
                <p role="alert" className="text-sm text-danger-fg">
                  {errorMessage(startSession.error)}
                </p>
              )}
            </>
          )}
        </div>
      </Panel>
      <NotesThread findingId={findingId} stage="retest" scope="stage" />
    </div>
  );
}
