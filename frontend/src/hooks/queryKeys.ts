// Centralised react-query cache keys so mutations can invalidate precisely.

export const queryKeys = {
  reports: ["reports"] as const,
  report: (id: number) => ["reports", id] as const,
  findings: (reportId?: number) => ["findings", reportId ?? "all"] as const,
  findingVersions: (findingId: number) => ["findingVersions", findingId] as const,
  notes: (findingId: number) => ["notes", findingId] as const,
  verdicts: ["verdicts"] as const,
  settings: ["settings"] as const,
  chats: ["chats"] as const,
  // A distinct root (not ["chats", id]) so invalidating the list doesn't also
  // refetch a thread — the send mutation writes the reply via setQueryData and
  // that must stay authoritative (FR-18).
  chat: (id: number) => ["chat", id] as const,
  findingSessions: (findingId: number) => ["findingSessions", findingId] as const,
  // keep a single session key; RetestSession.tsx will be switched to this in Task 9
  retestSession: (id: number) => ["retest-session", id] as const,
};
