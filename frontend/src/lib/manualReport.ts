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
}

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export function emptyFinding(): FindingDraft {
  return { title: "", severity: "high", description: "", endpoints: "", steps: "" };
}

/** Split free text into trimmed, non-empty lines. */
export function toLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Build the API payload from the form drafts (trim + split endpoints). */
export function draftsToPayload(label: string, drafts: FindingDraft[]): ManualReportInput {
  return {
    label: label.trim(),
    findings: drafts.map((draft) => ({
      title: draft.title.trim(),
      severity: draft.severity,
      description: draft.description.trim(),
      endpoints: toLines(draft.endpoints),
      steps_to_reproduce: draft.steps.trim(),
    })),
  };
}
