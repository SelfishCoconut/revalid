// Pure helpers for the manual-report form (kept out of the component file so
// fast-refresh stays happy and the logic is unit-testable in isolation).

import type { ManualReportInput, Severity } from "../api/types";

/** One finding as edited in the form; endpoints/steps are free text (one per line). */
export interface FindingDraft {
  title: string;
  severity: Severity;
  description: string;
  endpoints: string;
  steps: string;
  /** CVSS base vector, as stated by the source report (author-stated, #237). */
  cvssVector: string;
  /** CVSS base score; free text so a half-typed number never becomes 0. */
  cvssScore: string;
  /** ATT&CK technique ids, comma- or newline-separated. */
  techniques: string;
}

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export function emptyFinding(): FindingDraft {
  return {
    title: "",
    severity: "high",
    description: "",
    endpoints: "",
    steps: "",
    cvssVector: "",
    cvssScore: "",
    techniques: "",
  };
}

/** Split on commas or newlines — operators paste ATT&CK ids either way. */
export function toTechniques(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Parse the typed CVSS base score. Anything that is not a real number in range
 * becomes `undefined` rather than `0`: an unparsable score must read as "not
 * stated", never as a genuine assessment of zero.
 */
export function toScore(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 10) return undefined;
  return parsed;
}

/** Split free text into trimmed, non-empty lines. */
export function toLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/**
 * Build the API payload from the form drafts (trim + split endpoints).
 *
 * Taxonomy keys are emitted **only when the operator typed something**, so a
 * blank CVSS box sends nothing at all rather than an empty vector — the
 * difference between "not stated" and "stated as empty" (#237).
 */
export function draftsToPayload(label: string, drafts: FindingDraft[]): ManualReportInput {
  return {
    label: label.trim(),
    findings: drafts.map((draft) => {
      const vector = draft.cvssVector.trim();
      const score = toScore(draft.cvssScore);
      const techniques = toTechniques(draft.techniques);
      return {
        title: draft.title.trim(),
        severity: draft.severity,
        description: draft.description.trim(),
        endpoints: toLines(draft.endpoints),
        steps_to_reproduce: draft.steps.trim(),
        ...(vector ? { cvssv3: vector } : {}),
        ...(score !== undefined ? { cvssv3_score: score } : {}),
        ...(techniques.length > 0 ? { mitre_techniques: techniques } : {}),
      };
    }),
  };
}
