import type { ReactNode } from "react";

/** Mono, letter-spaced, uppercase micro-label — the instrument's field tags. */
export function Eyebrow({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`font-mono text-[11px] font-medium uppercase tracking-[0.2em] text-faint ${className}`}
    >
      {children}
    </span>
  );
}

/** A raised console panel: hairline-etched surface floating over the housing. */
export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-line bg-panel/80 shadow-[var(--panel-shadow)] backdrop-blur-sm ${className}`}
    >
      {children}
    </section>
  );
}

/** Standard panel header row: an eyebrow tag on the left, optional aside right. */
export function PanelHeader({
  eyebrow,
  aside,
  className = "",
}: {
  eyebrow: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 ${className}`}
    >
      <Eyebrow>{eyebrow}</Eyebrow>
      {aside}
    </div>
  );
}
