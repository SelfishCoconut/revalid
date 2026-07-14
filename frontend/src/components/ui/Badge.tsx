import type { Tone } from "../../lib/status";
import { TONE_PILL } from "../../lib/status";

const BASE =
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[11px] ring-1 ring-inset";

const EMPHASIS = {
  // Mixed-case status labels (report/plan/verdict).
  default: "font-medium tracking-wide",
  // Upper-case, letter-spaced enumerations (severity).
  caps: "font-semibold uppercase tracking-[0.12em]",
} as const;

/** Colour-coded status/severity pill in one of the tool's tones. */
export function Badge({
  tone,
  label,
  emphasis = "default",
}: {
  tone: Tone;
  label: string;
  emphasis?: keyof typeof EMPHASIS;
}) {
  return (
    <span className={`${BASE} ${EMPHASIS[emphasis]} ${TONE_PILL[tone]}`}>
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}
