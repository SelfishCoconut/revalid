// Same-origin API client. Every request targets a relative path under `/api`
// so the SPA works both behind the FastAPI process (prod) and the Vite proxy
// (dev). Non-2xx responses raise `ApiError` carrying the HTTP status and the
// FastAPI `detail` string so the UI can surface actionable messages.

import type {
  Finding,
  FindingEdit,
  FindingStage,
  FindingVersion,
  ManualReportInput,
  Note,
  Plan,
  PlannedAction,
  ProbeInput,
  ProbeResult,
  Report,
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

export function listReports(): Promise<Report[]> {
  return request<Report[]>("/reports");
}

export function getReport(id: number): Promise<Report> {
  return request<Report>(`/reports/${String(id)}`);
}

/** Upload a PDF report (multipart form field `file`); backend replies 202. */
export function uploadReport(file: File): Promise<Report> {
  const form = new FormData();
  form.append("file", file);
  return request<Report>("/reports", { method: "POST", body: form });
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

// --- Plans ---------------------------------------------------------------

/**
 * Start async plan generation (backend replies 202 with a `generating` version).
 * `instructions` is optional operator guidance woven into this generation and
 * recorded in the plan's lineage. Re-calling supersedes any live version — an
 * approved one included — so it doubles as "regenerate" (ADR-0022/0023).
 */
export function generatePlan(findingId: number, instructions = ""): Promise<Plan> {
  return request<Plan>(`/findings/${String(findingId)}/plan`, jsonInit("POST", { instructions }));
}

/** Replace the proposed plan's actions; backend re-gates each action. */
export function editPlan(findingId: number, actions: PlannedAction[]): Promise<Plan> {
  return request<Plan>(`/findings/${String(findingId)}/plan`, jsonInit("PUT", actions));
}

export function approvePlan(findingId: number): Promise<Plan> {
  return request<Plan>(`/findings/${String(findingId)}/plan/approve`, { method: "POST" });
}

export function rejectPlan(findingId: number): Promise<Plan> {
  return request<Plan>(`/findings/${String(findingId)}/plan/reject`, { method: "POST" });
}

/** Un-approve the approved plan back into an editable proposed copy (ADR-0023). */
export function revisePlan(findingId: number): Promise<Plan> {
  return request<Plan>(`/findings/${String(findingId)}/plan/revise`, { method: "POST" });
}

export function listPlans(findingId: number): Promise<Plan[]> {
  return request<Plan[]>(`/findings/${String(findingId)}/plans`);
}

// --- Retest / verdicts ---------------------------------------------------

export function retest(findingId: number): Promise<Verdict[]> {
  return request<Verdict[]>(`/findings/${String(findingId)}/retest`, { method: "POST" });
}

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

// --- Agentic retest sessions (FR-17) ---------------------------------------

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
  events: SessionEvent[];
}

/** Start an agentic retest session for a finding (backend replies with the new session). */
export function startRetestSession(findingId: number): Promise<RetestSession> {
  return request<RetestSession>(`/findings/${String(findingId)}/retest-session`, {
    method: "POST",
  });
}

export function getRetestSession(id: number): Promise<RetestSession> {
  return request<RetestSession>(`/retest-sessions/${String(id)}`);
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
 * Build the absolute WS(S) URL for a session's live transcript stream.
 * WebSocket has no relative-URL form, so this resolves against the current
 * page's origin/protocol the way `fetch`'s relative `API_BASE` paths do
 * implicitly — mirroring dev (Vite proxy) and prod (single uvicorn process).
 */
export function retestSocketUrl(id: number): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${API_BASE}/retest-sessions/${String(id)}/stream`;
}
