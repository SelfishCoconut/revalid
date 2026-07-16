import type { PlannedAction, Probe } from "../api/types";

/** Editable form row derived from a probe; carries non-edited fields through. */
export interface EditableAction {
  method: string;
  target: string;
  expected_indicator: string;
  headers: Record<string, string>;
  json_body: Record<string, unknown> | null;
}

export function toEditable(probe: Probe): EditableAction {
  return {
    method: probe.method,
    target: probe.url,
    expected_indicator: probe.expected_indicator,
    headers: probe.headers,
    json_body: probe.json_body,
  };
}

export function toPlannedAction(action: EditableAction): PlannedAction {
  return {
    method: action.method,
    target: action.target,
    headers: action.headers,
    json_body: action.json_body,
    expected_indicator: action.expected_indicator,
  };
}
