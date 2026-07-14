import { STATUS_META, type KnownStatus } from "../lib/status";
import { Badge } from "./ui/Badge";

/** Colour-coded pill for any known report/plan/verdict status string. */
export function StatusBadge({ status }: { status: KnownStatus }) {
  const meta = STATUS_META[status];
  return <Badge tone={meta.tone} label={meta.label} />;
}
