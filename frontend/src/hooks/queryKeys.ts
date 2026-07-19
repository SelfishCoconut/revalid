// Centralised react-query cache keys so mutations can invalidate precisely.

export const queryKeys = {
  reports: ["reports"] as const,
  report: (id: number) => ["reports", id] as const,
  findings: (reportId?: number) => ["findings", reportId ?? "all"] as const,
  findingVersions: (findingId: number) => ["findingVersions", findingId] as const,
  notes: (findingId: number) => ["notes", findingId] as const,
  verdicts: ["verdicts"] as const,
  settings: ["settings"] as const,
  findingSessions: (findingId: number) => ["findingSessions", findingId] as const,
  // keep a single session key; RetestSession.tsx will be switched to this in Task 9
  retestSession: (id: number) => ["retest-session", id] as const,
};
