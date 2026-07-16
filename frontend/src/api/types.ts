// Hand-typed mirror of the backend Pydantic models (no codegen). Keep in sync
// with the FastAPI schemas that back the `/api` endpoints.

export type ReportStatus = "extracting" | "ready" | "failed";

export interface Report {
  id: number;
  filename: string;
  status: ReportStatus;
  model: string;
  error: string | null;
  finding_count: number;
  created_at: string;
}

export type Severity = "info" | "low" | "medium" | "high" | "critical";

export interface Finding {
  id: number;
  report_id: number | null;
  /** Current version number of the finding's content (extraction = 1). */
  version: number;
  title: string;
  severity: Severity;
  description: string;
  impact: string;
  attack_vector: string;
  affected_endpoints: string[];
  reproduction_steps: string[];
  raw: Record<string, unknown>;
}

/** The five pipeline stages a note can be tagged with, plus `general` (FR-16). */
export type FindingStage =
  | "extract"
  | "plan"
  | "approve"
  | "retest"
  | "verdict"
  | "general";

/** One immutable version of a finding's content (FR-16 revision history). */
export interface FindingVersion {
  version: number;
  origin: "extraction" | "edit";
  edited_by: string | null;
  reason: string;
  created_at: string;
  title: string;
  severity: Severity;
  description: string;
  impact: string;
  attack_vector: string;
  affected_endpoints: string[];
  reproduction_steps: string[];
  raw: Record<string, unknown>;
}

/** Operator edit of a finding's content — POST /api/findings/{id} (FR-16). */
export interface FindingEdit {
  title: string;
  severity: Severity;
  description: string;
  impact: string;
  attack_vector: string;
  affected_endpoints: string[];
  reproduction_steps: string[];
  reason: string;
}

/** One append-only, stage-tagged operator note on a finding (FR-16). */
export interface Note {
  id: number;
  finding_id: number;
  stage: FindingStage;
  body: string;
  author: string;
  created_at: string;
}

/** One finding as entered manually / via JSON upload (bypasses LLM ingestion). */
export interface ManualFindingInput {
  title: string;
  severity: Severity;
  description?: string;
  endpoints?: string[];
  steps_to_reproduce?: string;
}

/** Payload for creating a report by hand — POST /api/reports/manual. */
export interface ManualReportInput {
  label: string;
  findings: ManualFindingInput[];
}

export interface PlannedAction {
  method: string;
  target: string;
  headers: Record<string, string>;
  json_body: Record<string, unknown> | null;
  expected_indicator: string;
}

export interface Probe {
  kind: string;
  method: string;
  url: string;
  headers: Record<string, string>;
  json_body: Record<string, unknown> | null;
  expected_indicator: string;
}

export interface RejectedAction {
  action: PlannedAction;
  reason: string;
}

export type PlanStatus =
  | "generating"
  | "proposed"
  | "approved"
  | "rejected"
  | "superseded"
  | "failed";

export interface Plan {
  id: number;
  finding_id: number;
  version: number;
  status: PlanStatus;
  origin: string;
  /** Set only on a `failed` version: why background generation produced no plan. */
  error: string | null;
  actions: Probe[];
  rejected_actions: RejectedAction[];
  raw: Record<string, unknown>;
  decided_at: string | null;
  decided_by: string | null;
}

export interface Evidence {
  request_method: string;
  request_url: string;
  request_body: string;
  response_status: number;
  response_headers: Record<string, string>;
  response_body_excerpt: string;
  elapsed_ms: number;
}

export type VerdictStatus = "still_open" | "fixed" | "inconclusive";

export interface Verdict {
  id: number;
  finding_id: number;
  probe_kind: string;
  plan_version: number | null;
  status: VerdictStatus;
  reason_code: string;
  rationale: string;
  matched_indicators: string[];
  evidence: Evidence;
}

export interface Settings {
  model: string;
  base_url: string | null;
  api_key_set: boolean;
  api_key_hint: string | null;
}

export interface SettingsUpdate {
  model: string;
  base_url: string | null;
  api_key?: string | null;
  clear_key?: boolean;
}

export interface ProbeInput {
  base_url: string | null;
  api_key?: string | null;
}

export interface ProbeResult {
  reachable: boolean;
  models: string[];
  error: string | null;
}
