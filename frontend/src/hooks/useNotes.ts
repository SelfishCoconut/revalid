import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { addNote, listNotes } from "../api/client";
import type { FindingStage, Note } from "../api/types";
import { queryKeys } from "./queryKeys";

/** A finding's notes, newest first (FR-16). */
export function useNotes(findingId: number) {
  return useQuery({
    queryKey: queryKeys.notes(findingId),
    queryFn: () => listNotes(findingId),
    enabled: Number.isFinite(findingId),
  });
}

/** Append a stage-tagged note; refreshes the finding's notes log (FR-16). */
export function useAddNote(findingId: number) {
  const client = useQueryClient();
  return useMutation<Note, Error, { stage: FindingStage; body: string }>({
    mutationFn: ({ stage, body }) => addNote(findingId, stage, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.notes(findingId) });
    },
  });
}
