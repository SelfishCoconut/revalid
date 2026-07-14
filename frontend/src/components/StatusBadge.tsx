import type { PlanStatus, ReportStatus, VerdictStatus } from "../api/types";

/**
 * Tones follow the tool's two-voice colour language: `iris` marks states where
 * the *system* is the actor (extraction running, a plan the AI proposed), while
 * the red/amber/green triad marks reality's outcomes (ready, approved, verdicts).
 */
type Tone = "iris" | "ok" | "warn" | "danger" | "neutral";

const TONE_STYLES: Record<Tone, string> = {
  iris: "text-iris-fg bg-iris/12 ring-iris/30",
  ok: "text-ok-fg bg-ok/12 ring-ok/30",
  warn: "text-warn-fg bg-warn/12 ring-warn/30",
  danger: "text-danger-fg bg-danger/12 ring-danger/30",
  neutral: "text-faint bg-faint/10 ring-faint/20",
};

type KnownStatus = ReportStatus | PlanStatus | VerdictStatus;

const STATUS_META: Record<KnownStatus, { tone: Tone; label: string }> = {
  // Report — the system is extracting; then reality is ready/failed.
  extracting: { tone: "iris", label: "extracting" },
  ready: { tone: "ok", label: "ready" },
  failed: { tone: "danger", label: "failed" },
  // Plan — the AI proposes; the human/gate decides.
  proposed: { tone: "iris", label: "proposed" },
  approved: { tone: "ok", label: "approved" },
  rejected: { tone: "danger", label: "rejected" },
  superseded: { tone: "neutral", label: "superseded" },
  // Verdict — reality's determination.
  still_open: { tone: "danger", label: "still open" },
  fixed: { tone: "ok", label: "fixed" },
  inconclusive: { tone: "warn", label: "inconclusive" },
};

/** Colour-coded pill for any known report/plan/verdict status string. */
export function StatusBadge({ status }: { status: KnownStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[11px] font-medium tracking-wide ring-1 ring-inset ${TONE_STYLES[meta.tone]}`}
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {meta.label}
    </span>
  );
}
