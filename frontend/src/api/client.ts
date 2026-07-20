// Same-origin API client. Every request targets a relative path under `/api`
// so the SPA works both behind the FastAPI process (prod) and the Vite proxy
// (dev). Non-2xx responses raise `ApiError` carrying the HTTP status and the
// FastAPI `detail` string so the UI can surface actionable messages.

import type {
  BackendStatus,
  DuplicateReport,
  Finding,
  FindingEdit,
  FindingStage,
  FindingVersion,
  ManualReportInput,
  Note,
  ProbeInput,
  ProbeResult,
  Report,
  ReportMetadata,
  RetestSessionSummary,
  Settings,
  SettingsUpdate,
  Verdict,
} from "./types";

const API_BASE = "/api";

/** Error thrown for any non-2xx API response. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (detail != null) return JSON.stringify(detail);
    }
  } catch {
    // Non-JSON error body — fall through to the status text.
  }
  return response.statusText || `HTTP ${String(response.status)}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// --- Reports -------------------------------------------------------------

/** List reports — active by default, archived when `archived` is true (#128). */
export function listReports(archived = false): Promise<Report[]> {
  return request<Report[]>(`/reports${archived ? "?archived=true" : ""}`);
}

export function getReport(id: number): Promise<Report> {
  return request<Report>(`/reports/${String(id)}`);
}

/** Archive or unarchive a report — a reversible soft-hide (#128). */
export function setReportArchived(id: number, archived: boolean): Promise<Report> {
  return request<Report>(`/reports/${String(id)}`, jsonInit("PATCH", { archived }));
}

/** Permanently delete a report and everything derived from it (#128). */
export function deleteReport(id: number): Promise<void> {
  return request<void>(`/reports/${String(id)}`, { method: "DELETE" });
}

/** Replace a report's document metadata with operator edits (#133). */
export function updateReportMetadata(id: number, metadata: ReportMetadata): Promise<Report> {
  return request<Report>(`/reports/${String(id)}/metadata`, jsonInit("PUT", metadata));
}

/** Thrown when an upload's bytes match an existing report's hash (#134). */
export class DuplicateReportError extends Error {
  readonly duplicates: DuplicateReport[];

  constructor(duplicates: DuplicateReport[]) {
    super("This report has already been uploaded.");
    this.name = "DuplicateReportError";
    this.duplicates = duplicates;
  }
}

/**
 * Upload a PDF report (multipart field `file`); backend replies 202. If the
 * bytes match an existing report and `force` is not set, the backend replies 409
 * and this throws {@link DuplicateReportError} carrying the matches (#134).
 */
export async function uploadReport(file: File, force = false): Promise<Report> {
  const form = new FormData();
  form.append("file", file);
  const query = force ? "?force=true" : "";
  const response = await fetch(`${API_BASE}/reports${query}`, { method: "POST", body: form });
  if (response.status === 409) {
    const body: unknown = await response.json().catch(() => null);
    const detail = (body as { detail?: { duplicates?: DuplicateReport[] } } | null)?.detail;
    throw new DuplicateReportError(detail?.duplicates ?? []);
  }
  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }
  return (await response.json()) as Report;
}

/**
 * Create a report and its findings directly, bypassing LLM extraction — the
 * human-entry escape hatch (form or JSON upload). Backend replies 201 with a
 * `ready` report.
 */
export function createManualReport(input: ManualReportInput): Promise<Report> {
  return request<Report>("/reports/manual", jsonInit("POST", input));
}

// --- Findings ------------------------------------------------------------

export function listFindings(reportId?: number): Promise<Finding[]> {
  const query = reportId != null ? `?report_id=${String(reportId)}` : "";
  return request<Finding[]>(`/findings${query}`);
}

/** Record an operator edit as a new immutable finding version (FR-16). */
export function editFinding(findingId: number, body: FindingEdit): Promise<Finding> {
  return request<Finding>(`/findings/${String(findingId)}`, jsonInit("POST", body));
}

/** Every version of a finding, oldest first (extraction = v1) — FR-16 history. */
export function listFindingVersions(findingId: number): Promise<FindingVersion[]> {
  return request<FindingVersion[]>(`/findings/${String(findingId)}/versions`);
}

/** Append a stage-tagged note to a finding's log (FR-16). */
export function addNote(findingId: number, stage: FindingStage, body: string): Promise<Note> {
  return request<Note>(`/findings/${String(findingId)}/notes`, jsonInit("POST", { stage, body }));
}

/** A finding's notes, newest first (FR-16). */
export function listNotes(findingId: number): Promise<Note[]> {
  return request<Note[]>(`/findings/${String(findingId)}/notes`);
}

// --- Verdicts --------------------------------------------------------------

export function listVerdicts(): Promise<Verdict[]> {
  return request<Verdict[]>("/verdicts");
}

// --- Settings --------------------------------------------------------------

export function getSettings(): Promise<Settings> {
  return request<Settings>("/settings");
}

export function updateSettings(body: SettingsUpdate): Promise<Settings> {
  return request<Settings>("/settings", jsonInit("PUT", body));
}

export function probeProvider(body: ProbeInput): Promise<ProbeResult> {
  return request<ProbeResult>("/settings/probe", jsonInit("POST", body));
}

/** Live backend reachability + active model, for the sidebar status pill. */
export function getBackendStatus(): Promise<BackendStatus> {
  return request<BackendStatus>("/settings/status");
}

// --- Agentic retest sessions (FR-17) ---------------------------------------

/** Generate a retest-goal draft for a finding, pre-session (FR-17 6b-iii-b). */
export function draftGoal(findingId: number): Promise<{ steps: string[] }> {
  return request<{ steps: string[] }>(`/findings/${String(findingId)}/goal/draft`, {
    method: "POST",
  });
}

/** List a finding's retest sessions, newest first (FR-17 6b-iii-b). */
export function listRetestSessions(findingId: number): Promise<RetestSessionSummary[]> {
  return request<RetestSessionSummary[]>(`/findings/${String(findingId)}/retest-sessions`);
}

/** One ordered event from a retest session's transcript (see the WS stream). */
export interface SessionEvent {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
}

/** A retest session's full state, as returned by the start/get endpoints. */
export interface RetestSession {
  id: number;
  finding_id: number;
  status: string;
  model: string;
  verdict_status: string | null;
  verdict_rationale: string | null;
  free_launch: boolean;
  events: SessionEvent[];
}

/** Free-launch + seed goal for a new session (FR-17 Slice 5); all optional. */
export interface StartSessionOptions {
  free_launch?: boolean;
  /** A pre-start user-owned goal (FR-17 6b-iii-b); seeded verbatim if present. */
  initial_goal?: string[];
  /** The retest scope — exact target URL(s) the agent may hit (FR-17). Set at
   * launch (reachability is fixed when the sandbox is provisioned); defaults to
   * the finding's endpoints server-side when omitted. */
  target_endpoints?: string[];
}

/** Start an agentic retest session for a finding (backend replies with the new session). */
export function startRetestSession(
  findingId: number,
  opts?: StartSessionOptions,
): Promise<RetestSession> {
  return request<RetestSession>(
    `/findings/${String(findingId)}/retest-session`,
    opts ? jsonInit("POST", opts) : { method: "POST" },
  );
}

export function getRetestSession(id: number): Promise<RetestSession> {
  return request<RetestSession>(`/retest-sessions/${String(id)}`);
}

/**
 * Toggle free-launch mode on a live session (FR-17 Slice 5). Enabling
 * auto-approves the agent's commands (plan changes stay gated) and drives any
 * pending command; disabling re-arms the per-command gate.
 */
export function setFreeLaunch(id: number, enabled: boolean): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/free-launch`,
    jsonInit("POST", { enabled }),
  );
}

/** Approve a proposed command so the agent runs it against the allowlisted lab target. */
export function approveCommand(id: number, cid: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/retest-sessions/${String(id)}/commands/${cid}/approve`, {
    method: "POST",
  });
}

/** Reject a proposed command; an optional reason is recorded on the session. */
export function rejectCommand(id: number, cid: string, reason = ""): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/commands/${cid}/reject`,
    jsonInit("POST", { reason }),
  );
}

export function endRetestSession(id: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/retest-sessions/${String(id)}/end`, { method: "POST" });
}

/**
 * Keep going on a session paused for guidance (ADR-0034): resume the agent. A
 * no-op server-side unless the session is paused with a live agent.
 */
export function continueSession(id: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/retest-sessions/${String(id)}/continue`, { method: "POST" });
}

/**
 * Manually conclude a session with the operator's determination (ADR-0034) — the
 * only path that records `inconclusive`. Records the verdict and tears the sandbox
 * down.
 */
export function concludeSession(
  id: number,
  status: string,
  rationale: string,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/conclude`,
    jsonInit("POST", { status, rationale }),
  );
}

/**
 * Adjudicate a concluded session's verdict (FR-17 Slice 6a): accept the agent's
 * call (pass its own status) or override it (a different status). Appends a
 * superseding operator verdict; the agent's record is never mutated.
 */
export function adjudicateSession(
  id: number,
  status: string,
  rationale: string,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/adjudicate`,
    jsonInit("POST", { status, rationale }),
  );
}

/**
 * Run a manual operator command (`!`) in the session's sandbox — ungated,
 * discrete exec (FR-17 Slice 2). Its output lands in the shared terminal and
 * the agent observes it on its next turn.
 */
export function submitHumanCommand(id: number, command: string): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/human-command`,
    jsonInit("POST", { command }),
  );
}

/**
 * Send a free-text chat message to the retest agent (FR-17 Slice 4). Queued
 * server-side and delivered to the agent as a user turn on its next
 * approve/reject (pure-queue steering).
 */
export function submitMessage(id: number, text: string): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/message`,
    jsonInit("POST", { text }),
  );
}

/**
 * Set the user-owned goal for a session (FR-17 6b-ii). Updates the "Current goal"
 * panel and is delivered to the agent on its next turn (pure-queue).
 */
export function setSessionGoal(id: number, steps: string[]): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/retest-sessions/${String(id)}/goal`,
    jsonInit("POST", { steps }),
  );
}

/** Regenerate the goal for a session's finding via the LLM (FR-17 6b-ii). */
export function regenerateSessionGoal(id: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/retest-sessions/${String(id)}/goal/regenerate`, {
    method: "POST",
  });
}

/**
 * Build the absolute WS(S) URL for a session's live transcript stream.
 * WebSocket has no relative-URL form, so this resolves against the current
 * page's origin/protocol the way `fetch`'s relative `API_BASE` paths do
 * implicitly — mirroring dev (Vite proxy) and prod (single uvicorn process).
 */
export function retestSocketUrl(id: number): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${API_BASE}/retest-sessions/${String(id)}/stream`;
}
