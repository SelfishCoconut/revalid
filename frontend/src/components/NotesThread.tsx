import { useState } from "react";

import type { FindingStage } from "../api/types";
import { useAddNote, useNotes } from "../hooks/useNotes";
import { formatDate, useDateFormat } from "../lib/dateFormat";
import { errorMessage } from "../lib/format";
import { Spinner } from "./Spinner";
import { Button } from "./ui/Button";
import { Panel, PanelHeader } from "./ui/Panel";

/**
 * A finding's append-only notes log (FR-16). New notes are tagged with `stage` —
 * the stage the operator is on — and the log is never edited or deleted. `scope`
 * decides whether this instance shows just this stage's notes (on a stage page)
 * or the whole thread (the finding header).
 */
export function NotesThread({
  findingId,
  stage,
  scope,
}: {
  findingId: number;
  stage: FindingStage;
  scope: "stage" | "all";
}) {
  const notes = useNotes(findingId);
  const addNote = useAddNote(findingId);
  const dateFormat = useDateFormat();
  const [draft, setDraft] = useState("");

  const all = notes.data ?? [];
  const shown = scope === "stage" ? all.filter((note) => note.stage === stage) : all;

  function submit() {
    const body = draft.trim();
    if (!body) return;
    addNote.mutate(
      { stage, body },
      {
        onSuccess: () => {
          setDraft("");
        },
      },
    );
  }

  return (
    <Panel>
      <PanelHeader
        eyebrow="Notes"
        aside={
          <span className="font-mono text-[11px] text-faint">
            {shown.length} {scope === "stage" ? "on this stage" : "total"}
          </span>
        }
      />
      <div className="space-y-3 p-4">
        {notes.isPending ? (
          <Spinner label="Loading notes" />
        ) : shown.length === 0 ? (
          <p className="text-sm text-faint">No notes yet.</p>
        ) : (
          <ul className="space-y-2">
            {shown.map((note) => (
              <li
                key={note.id}
                className="rounded-lg border border-line bg-panel-2/40 px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="rounded bg-iris/12 px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-iris-fg ring-1 ring-inset ring-iris/25">
                    {note.stage}
                  </span>
                  <span className="font-mono text-[11px] text-faint">
                    {note.author} · {formatDate(note.created_at, dateFormat)}
                  </span>
                </div>
                <p className="mt-1.5 text-[13px] whitespace-pre-wrap text-dim">{note.body}</p>
              </li>
            ))}
          </ul>
        )}

        <div className="space-y-2 border-t border-line pt-3">
          <textarea
            aria-label={`Add a note on the ${stage} stage`}
            rows={2}
            value={draft}
            disabled={addNote.isPending}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
            placeholder={`Leave a note on the ${stage} stage…`}
            className="w-full resize-y rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-fg transition-colors placeholder:text-faint focus:border-iris/60 disabled:opacity-55"
          />
          <div className="flex items-center gap-3">
            <Button variant="ghost" disabled={addNote.isPending || !draft.trim()} onClick={submit}>
              {addNote.isPending ? "Adding…" : "Add note"}
            </Button>
            {addNote.isError && (
              <span role="alert" className="text-sm text-danger-fg">
                {errorMessage(addNote.error)}
              </span>
            )}
          </div>
        </div>
      </div>
    </Panel>
  );
}
