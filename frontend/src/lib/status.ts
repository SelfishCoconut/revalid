import type { ReportStatus, Severity, VerdictStatus } from "../api/types";

/**
 * The tool's two-voice colour language, as tones. `iris` marks states where the
 * *system* is the actor (extraction running, an agentic session in progress); the
 * red/amber/green triad (`danger`/`warn`/`ok`) marks reality's outcomes; `high`
 * and `low` extend the triad for severity; `neutral` is the muted default.
 *
 * Tone is the single source of the status→colour knowledge. Each surface maps a
 * tone to its own utility strings (pill vs. solid fill vs. ring) locally, because
 * Tailwind needs the class names spelled out literally — but *which* tone a status
 * carries is decided only here.
 */
export type Tone = "iris" | "ok" | "warn" | "danger" | "high" | "low" | "neutral";

/** Every known report/verdict status → its tone and human label. */
export type KnownStatus = ReportStatus | VerdictStatus;

export const STATUS_META: Record<KnownStatus, { tone: Tone; label: string }> = {
  // Report — the system is extracting; then reality is ready/failed.
  extracting: { tone: "iris", label: "extracting" },
  ready: { tone: "ok", label: "ready" },
  failed: { tone: "danger", label: "failed" },
  // Verdict — reality's determination.
  still_open: { tone: "danger", label: "still open" },
  fixed: { tone: "ok", label: "fixed" },
  inconclusive: { tone: "warn", label: "inconclusive" },
};

/** Verdict → tone, derived from the single source above (used by the meter/track). */
export const VERDICT_TONE: Record<VerdictStatus, Tone> = {
  still_open: STATUS_META.still_open.tone,
  inconclusive: STATUS_META.inconclusive.tone,
  fixed: STATUS_META.fixed.tone,
};

/** Finding severity → tone. */
export const SEVERITY_TONE: Record<Severity, Tone> = {
  critical: "danger",
  high: "high",
  medium: "warn",
  low: "low",
  info: "neutral",
};

/** Tone → pill utility classes (text / translucent bg / ring). The badge surface. */
export const TONE_PILL: Record<Tone, string> = {
  iris: "text-iris-fg bg-iris/12 ring-iris/30",
  ok: "text-ok-fg bg-ok/12 ring-ok/30",
  warn: "text-warn-fg bg-warn/12 ring-warn/30",
  danger: "text-danger-fg bg-danger/12 ring-danger/30",
  high: "text-high-fg bg-high/12 ring-high/30",
  low: "text-low-fg bg-low/12 ring-low/30",
  neutral: "text-faint bg-faint/10 ring-faint/20",
};

/** Tone → solid fill (pipeline node fill, meter bar segments). */
export const TONE_FILL: Record<Tone, string> = {
  iris: "bg-iris",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  high: "bg-high",
  low: "bg-low",
  neutral: "bg-faint",
};

/** Tone → solid foreground text (meter readouts). */
export const TONE_TEXT: Record<Tone, string> = {
  iris: "text-iris-fg",
  ok: "text-ok-fg",
  warn: "text-warn-fg",
  danger: "text-danger-fg",
  high: "text-high-fg",
  low: "text-low-fg",
  neutral: "text-faint",
};

/** Tone → translucent ring (pipeline node ring). */
export const TONE_RING: Record<Tone, string> = {
  iris: "ring-iris/50",
  ok: "ring-ok/50",
  warn: "ring-warn/50",
  danger: "ring-danger/50",
  high: "ring-high/50",
  low: "ring-low/50",
  neutral: "ring-faint/50",
};
