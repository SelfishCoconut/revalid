// Same-origin API client. Every request targets a relative path under `/api`
// so the SPA works both behind the FastAPI process (prod) and the Vite proxy
// (dev). Non-2xx responses raise `ApiError` carrying the HTTP status and the
// FastAPI `detail` string so the UI can surface actionable messages.

import type {
  Finding,
  ManualReportInput,
  Plan,
  PlannedAction,
  Report,
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

// --- Plans ---------------------------------------------------------------

export function generatePlan(findingId: number): Promise<Plan> {
  return request<Plan>(`/findings/${String(findingId)}/plan`, { method: "POST" });
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
