// Hand-typed mirror of the backend Pydantic models (no codegen). Keep in sync
// with the FastAPI schemas that back the `/api` endpoints.

export type ReportStatus = "extracting" | "ready" | "failed" | "cancelled";

export interface Report {
  id: number;
  filename: string;
  status: ReportStatus;
  model: string;
  error: string | null;
  finding_count: number;
  /** Soft-hidden from the overview but kept and reversible (#128). */
  archived: boolean;
  /** SHA-256 of the uploaded bytes (null for manually-entered reports) (#134). */
  content_hash: string | null;
  /** Document-level metadata extracted from the report, operator-editable (#133). */
  metadata: ReportMetadata | null;
  created_at: string;
}

/** A person named in the report, with their role (#133). */
export interface Person {
  name: string;
  role: string;
}

/** Document-level metadata extracted from a report, operator-editable (#133). */
export interface ReportMetadata {
  product: string;
  report_date: string;
  author: string;
  people: Person[];
}

/** A prior report that matches an upload's hash, surfaced on the dedup warning (#134). */
export interface DuplicateReport {
  id: number;
  filename: string;
  created_at: string;
}

export type Severity = "info" | "low" | "medium" | "high" | "critical";

/**
 * CVSS severity code attached to a finding at ingestion (FR-19).
 *
 * `inferred` is provenance, not confidence: `false` means the code was read from
 * the report verbatim, `true` means the model derived it because the report
 * stated none. An empty `vector` with `inferred: false` means the report had no
 * CVSS code and none was derived — render it as absent, never as a zero score.
 */
export interface CvssCode {
  /** CVSS base vector, e.g. `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H`. */
  vector: string;
  base_score: number | null;
  inferred: boolean;
}

/**
 * MITRE ATT&CK technique mapping for a finding (FR-19). Same `inferred`
 * provenance rule as {@link CvssCode}; empty `techniques` means none stated and
 * none derived.
 */
export interface MitreMapping {
  /** ATT&CK technique IDs, e.g. `T1190`. */
  techniques: string[];
  inferred: boolean;
}

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
  cvss: CvssCode;
  mitre: MitreMapping;
  raw: Record<string, unknown>;
}

/**
 * The pipeline stages a note can be tagged with (FR-16): extract → goal →
 * retest → verdict, plus `general` for a note left from the finding overview.
 *
 * `plan` and `approve` are the retired batch flow's stages (ADR-0033). Nothing
 * produces them — the goal stage tagged its notes `plan` until #113, and those
 * rows are renamed on startup — but they stay in the union so a note read back
 * from an older database still types.
 */
export type FindingStage =
  | "extract"
  | "goal"
  | "retest"
  | "verdict"
  | "general"
  /** @deprecated legacy, read-only (ADR-0033). */
  | "plan"
  /** @deprecated legacy, read-only (ADR-0033). */
  | "approve";

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
  cvss: CvssCode;
  mitre: MitreMapping;
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
  /**
   * Taxonomy the operator entered by hand (FR-19). Omit to leave the finding's
   * current values untouched — the server keeps them, provenance included.
   * There is no `inferred` field: the server decides that by comparing against
   * the current version, so the client cannot claim a model guess as its own.
   */
  cvss?: { vector: string; base_score: number | null };
  mitre?: { techniques: string[] };
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
  /**
   * Taxonomy the source report stated, typed by the operator (#237). Present
   * only when actually typed — omitted means "not stated", which is different
   * from an empty vector. Anything supplied here is recorded author-stated
   * (`inferred: false`) and is never overwritten by `enrich`.
   */
  cvssv3?: string;
  cvssv3_score?: number;
  /** ATT&CK technique ids. A revalid key — DefectDojo has no ATT&CK field. */
  mitre_techniques?: string[];
}

/** Payload for creating a report by hand — POST /api/reports/manual. */
export interface ManualReportInput {
  label: string;
  findings: ManualFindingInput[];
  /**
   * Opt into deriving CVSS + MITRE ATT&CK for each finding (FR-19, issue #233).
   * Omitted/false keeps this door entirely LLM-free — no model call, instant and
   * free. True costs one model call per finding.
   */
  enrich?: boolean;
}

/** Flexible, tool-agnostic proof for an agentic verdict (FR-17 Slice 6b-i). */
export interface AgenticEvidence {
  explanation: string;
  command: string;
  output: string;
  exit_code: number | null;
  elapsed_ms: number;
}

export type VerdictStatus = "still_open" | "fixed" | "inconclusive";

export interface Verdict {
  id: number;
  finding_id: number;
  status: VerdictStatus;
  reason_code: string;
  rationale: string;
  matched_indicators: string[];
  session_id: number | null;
  actor: string;
  evidence: AgenticEvidence | null;
}

/** A compact retest-session row for a finding's session list (FR-17 6b-iii-b). */
export interface RetestSessionSummary {
  id: number;
  finding_id: number;
  status: string;
  verdict_status: string | null;
  created_at: string;
}

export interface Settings {
  model: string;
  base_url: string | null;
  api_key_set: boolean;
  api_key_hint: string | null;
}

/** Live LLM-backend reachability + active model, for the sidebar status pill. */
export interface BackendStatus {
  connected: boolean;
  model: string;
}

export interface SettingsUpdate {
  model: string;
  base_url: string | null;
  api_key?: string | null;
  clear_key?: boolean;
}

/** Which provider to discover models from; drives the probe's auth scheme. */
export type ProviderKind = "ollama" | "anthropic" | "openai";

export interface ProbeInput {
  provider?: ProviderKind | null;
  base_url: string | null;
  api_key?: string | null;
}

export interface ProbeResult {
  reachable: boolean;
  models: string[];
  error: string | null;
}

// --- Reports chat (FR-18) --------------------------------------------------

/** A persisted reports-chat thread summary (FR-18). */
export interface ChatSummary {
  id: number;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
}

/** One persisted turn in a reports-chat thread (FR-18). */
export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

/** A thread plus its full ordered transcript (FR-18). */
export interface ChatDetail extends ChatSummary {
  messages: ChatMessage[];
}
