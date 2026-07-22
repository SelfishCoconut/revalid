import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { startRetestSession } from "../../api/client";
import type { RetestSessionSummary } from "../../api/types";
import { NotesThread } from "../../components/NotesThread";
import { RestartIcon, TerminalIcon } from "../../components/icons";
import { Button } from "../../components/ui/Button";
import { Eyebrow, Panel } from "../../components/ui/Panel";
import { queryKeys } from "../../hooks/queryKeys";
import { useFindingStage } from "../../hooks/useFindingStage";
import { useGoalDraft } from "../../hooks/useGoalDraft";
import { errorMessage } from "../../lib/format";
import { goalStepsToText, parseGoalSteps } from "../../lib/goal";

/** Stage 2 — draft + edit the retest goal, then launch a seeded agentic session (FR-17). */
export function GoalStage() {
  const { finding, findingId } = useFindingStage();
  const draft = useGoalDraft(findingId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  // Target scope — the exact endpoint(s) the agent may hit, set at launch. The
  // sandbox/egress-lock is provisioned around this, so it's fixed for the session
  // (Restart to change). Defaults to the finding's endpoints; the operator can add
  // or remove URLs before starting.
  const [endpoints, setEndpoints] = useState<string[]>(
    finding.affected_endpoints.length > 0 ? [...finding.affected_endpoints] : [""],
  );
  // Track which draft we've already seeded the textarea from, so a fresh draft
  // (first load or after Regenerate) re-seeds while further edits stay local.
  const [seededSteps, setSeededSteps] = useState<string[] | undefined>(undefined);

  // Adjust state during render, not in an effect (react.dev/learn/you-might-not-need-an-effect):
  // this is not "keeping state in sync" with an external system, it's deriving the
  // initial edit box from newly arrived query data.
  if (draft.data && draft.data.steps !== seededSteps) {
    setSeededSteps(draft.data.steps);
    setText(goalStepsToText(draft.data.steps));
  }

  // Opened **deferred** (#157): the session lands `idle` — scope and goal
  // recorded, but no sandbox provisioned and no LLM call made — and waits in the
  // console for an explicit "Wake the agent". Restart has always opened this way
  // (#150); now first launch does too, so a session never runs unbidden.
  const start = useMutation({
    mutationFn: () =>
      startRetestSession(findingId, {
        deferred: true,
        initial_goal: parseGoalSteps(text),
        target_endpoints: endpoints.map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: (created) => {
      // Seed the finding-sessions cache with the just-created session before
      // navigating: FindingLayout stays mounted across this child-stage
      // navigation, so without this the cache still holds the pre-start `[]`
      // and RetestStage bounces straight back to /goal (see FR-17 6b-iii-b
      // fix wave, GoalStage regression).
      queryClient.setQueryData<RetestSessionSummary[]>(
        queryKeys.findingSessions(findingId),
        (old) => [
          {
            id: created.id,
            finding_id: created.finding_id,
            status: created.status,
            verdict_status: created.verdict_status,
            created_at: new Date().toISOString(),
          },
          ...(old ?? []),
        ],
      );
      navigate(`/findings/${String(findingId)}/retest`);
    },
  });

  return (
    <div className="space-y-6">
      <Panel>
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <Eyebrow>Target scope</Eyebrow>
        </div>
        <div className="space-y-3 p-4">
          <p className="text-sm text-dim">
            The exact endpoint(s) the agent may hit. Set at launch — the egress-locked
            sandbox is provisioned around this scope, so changing it means restarting the
            retest.
          </p>
          <div className="space-y-2">
            {endpoints.map((ep, i) => (
              <div key={i} className="flex items-center gap-2">
                <input
                  aria-label={`target endpoint ${String(i + 1)}`}
                  value={ep}
                  onChange={(e) => {
                    setEndpoints((xs) => xs.map((x, j) => (j === i ? e.target.value : x)));
                  }}
                  placeholder="http://host:port/path"
                  className="min-w-0 flex-1 rounded border border-line bg-panel px-2 py-1 font-mono text-[13px] text-fg"
                />
                <Button
                  variant="ghost"
                  disabled={endpoints.length === 1}
                  onClick={() => {
                    setEndpoints((xs) => xs.filter((_, j) => j !== i));
                  }}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
          <Button
            variant="ghost"
            onClick={() => {
              setEndpoints((xs) => [...xs, ""]);
            }}
          >
            + Add endpoint
          </Button>
        </div>
      </Panel>
      <Panel>
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <Eyebrow>Retest goal</Eyebrow>
          <Button
            variant="ghost"
            disabled={draft.isFetching}
            onClick={() => void draft.refetch()}
          >
            <RestartIcon />
            {draft.isFetching ? "Generating…" : "Regenerate"}
          </Button>
        </div>
        <div className="space-y-3 p-4">
          <p className="text-sm text-dim">
            A generic, editable goal for the agent — one step per line. Edit it, then start the
            sandboxed retest; you can keep steering the goal live in the console.
          </p>
          <textarea
            aria-label="retest goal steps"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
            }}
            rows={5}
            placeholder="One verification step per line…"
            className="w-full rounded border border-line bg-panel px-2 py-1 font-mono text-[13px] text-fg"
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button
              disabled={start.isPending}
              onClick={() => {
                start.mutate();
              }}
            >
              <TerminalIcon />
              {start.isPending ? "Opening…" : "Open console"}
            </Button>
            <span className="font-mono text-[11px] text-faint">
              Opens the console with this goal — nothing runs until you wake the agent.
            </span>
          </div>
          {draft.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(draft.error)}
            </p>
          )}
          {start.isError && (
            <p role="alert" className="text-sm text-danger-fg">
              {errorMessage(start.error)}
            </p>
          )}
        </div>
      </Panel>
      <NotesThread findingId={findingId} stage="goal" scope="stage" />
    </div>
  );
}
