import type { Severity } from "../api/types";
import { SEVERITY_TONE } from "../lib/status";
import { Badge } from "./ui/Badge";

/** Colour-coded pill for a finding's severity. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={SEVERITY_TONE[severity]} label={severity} emphasis="caps" />;
}
