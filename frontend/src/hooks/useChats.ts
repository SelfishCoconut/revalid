import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { createChat, deleteChat, getChat, listChats, streamChatMessage } from "../api/client";
import type { ChatSummary } from "../api/types";
import { queryKeys } from "./queryKeys";

/** All reports-chat threads, most-recently-updated first (FR-18). */
export function useChats() {
  return useQuery({ queryKey: queryKeys.chats, queryFn: listChats });
}

/** One thread + its transcript; disabled until an id is known (FR-18). */
export function useChat(id: number | undefined) {
  return useQuery({
    queryKey: queryKeys.chat(id ?? 0),
    queryFn: () => getChat(id as number),
    enabled: id != null,
  });
}

/** Create a new thread; on success refresh the thread list (FR-18). */
export function useCreateChat() {
  const client = useQueryClient();
  return useMutation<ChatSummary, Error, void>({
    mutationFn: () => createChat(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.chats });
    },
  });
}

/**
 * Send a question and stream the assistant's reply token-by-token (FR-18).
 *
 * Token streaming doesn't fit TanStack's single-resolve mutation model, so this
 * drives the in-progress reply as local state (`streamed`, growing per token)
 * while `isStreaming` is true, then — once the stream drains — refetches the
 * authoritative persisted thread and refreshes the list (new title / order). The
 * `streamed` text is only cleared after that refetch lands, so the live bubble
 * hands off to the persisted one with no flicker.
 */
export function useStreamingSend(id: number) {
  const client = useQueryClient();
  const [streamed, setStreamed] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const send = useCallback(
    async (content: string) => {
      setIsStreaming(true);
      setStreamed("");
      setError(null);
      try {
        await streamChatMessage(id, content, (delta) => {
          setStreamed((prev) => (prev ?? "") + delta);
        });
        // Refetch the persisted thread (real ids/timestamps) before dropping the
        // live text, then refresh the rail for the thread's new title / order.
        await client.invalidateQueries({ queryKey: queryKeys.chat(id) });
        void client.invalidateQueries({ queryKey: queryKeys.chats });
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        setIsStreaming(false);
        setStreamed(null);
      }
    },
    [client, id],
  );

  return { send, isStreaming, streamed, error };
}

/** Delete a thread; on success refresh the list (FR-18). */
export function useDeleteChat() {
  const client = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: (id: number) => deleteChat(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.chats });
    },
  });
}
