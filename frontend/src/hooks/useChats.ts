import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createChat,
  deleteChat,
  getChat,
  listChats,
  sendChatMessage,
} from "../api/client";
import type { ChatDetail, ChatSummary } from "../api/types";
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
 * Send a question to a thread. The reply is the full updated thread, so we prime
 * its cache immediately (no refetch flicker) and refresh the list for its new
 * title / order (FR-18).
 */
export function useSendMessage(id: number) {
  const client = useQueryClient();
  return useMutation<ChatDetail, Error, string>({
    mutationFn: (content: string) => sendChatMessage(id, content),
    onSuccess: (detail) => {
      client.setQueryData(queryKeys.chat(id), detail);
      void client.invalidateQueries({ queryKey: queryKeys.chats });
    },
  });
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
