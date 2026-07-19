import { useState } from "react";

import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { startRetestSession } from "../../api/client";
import { NotesThread } from "../../components/NotesThread";
import { Button } from "../../components/ui/Button";
import { Eyebrow, Panel } from "../../components/ui/Panel";
import { useFindingStage } from "../../hooks/useFindingStage";
import { useGoalDraft } from "../../hooks/useGoalDraft";
import { errorMessage } from "../../lib/format";

/** Stage 2 — draft + edit the retest goal, then launch a seeded agentic session (FR-17). */
export function GoalStage() {
  const { findingId } = useFindingStage();
  const draft = useGoalDraft(findingId);
  const navigate = useNavigate();
  const [text, setText] = useState("");
  // Track which draft we've already seeded the textarea from, so a fresh draft
  // (first load or after Regenerate) re-seeds while further edits stay local.
  const [seededSteps, setSeededSteps] = useState<string[] | undefined>(undefined);

  // Adjust state during render, not in an effect (react.dev/learn/you-might-not-need-an-effect):
  // this is not "keeping state in sync" with an external system, it's deriving the
  // initial edit box from newly arrived query data.
  if (draft.data && draft.data.steps !== seededSteps) {
    setSeededSteps(draft.data.steps);
    setText(draft.data.steps.join("\n"));
  }

  const start = useMutation({
    mutationFn: () =>
      startRetestSession(findingId, {
        initial_goal: text.split("\n").map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      navigate(`/findings/${String(findingId)}/retest`);
    },
  });

  return (
    <div className="space-y-6">
      <Panel>
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <Eyebrow>Retest goal</Eyebrow>
          <Button
            variant="ghost"
            disabled={draft.isFetching}
            onClick={() => void draft.refetch()}
          >
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
              {start.isPending ? "Starting…" : "Start retest"}
            </Button>
            <span className="font-mono text-[11px] text-faint">
              Launches the egress-locked agent with this goal.
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
      <NotesThread findingId={findingId} stage="plan" scope="stage" />
    </div>
  );
}
