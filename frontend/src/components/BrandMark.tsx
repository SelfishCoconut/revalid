/**
 * The instrument mark: a scope reticle framing a live iris core — the tool
 * holds a finding under examination and reports what it sees.
 */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect
        x="1.25"
        y="1.25"
        width="29.5"
        height="29.5"
        rx="8"
        className="fill-panel-2 stroke-line-2"
        strokeWidth="1.5"
      />
      <circle
        cx="16"
        cy="16"
        r="8.5"
        className="stroke-line-2"
        strokeWidth="1.25"
      />
      <path
        d="M16 3.5v6M16 22.5v6M3.5 16h6M22.5 16h6"
        className="stroke-faint"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
      <circle cx="16" cy="16" r="3.4" className="fill-iris" />
      <circle cx="16" cy="16" r="6" className="stroke-iris/40" strokeWidth="1.25" />
    </svg>
  );
}
