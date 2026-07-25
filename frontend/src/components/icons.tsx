/**
 * The console's icon set — hand-rolled inline SVG, no icon dependency (#157).
 *
 * Follows the precedent set by `Sidebar.tsx`: a 16-unit viewBox drawn in
 * `currentColor` so an icon inherits its button's text colour in both themes,
 * `aria-hidden` because every icon here sits beside a real text label, and
 * `shrink-0` so it never squashes in a flex row. Keeping them local (rather
 * than pulling `lucide-react`) holds the offline posture (NFR-03) — nothing is
 * fetched at runtime and the bundle carries only the glyphs actually used.
 */

/** Shared geometry for every glyph: size, colour inheritance, a11y, layout. */
function Glyph({ children, size = 14 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

/** Power symbol — waking a sleeping (idle) session. */
export function PowerIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M8 1.8v6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M4.6 4.2a4.8 4.8 0 1 0 6.8 0"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

/** Checkmark — approve a proposed command. */
export function CheckIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="m2.8 8.4 3.2 3.2 7.2-7.2"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Cross — reject a proposed command. */
export function CrossIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M3.6 3.6 12.4 12.4M12.4 3.6 3.6 12.4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

/** Two upright bars — pause a running session (Stop). */
export function PauseIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <rect x="4" y="3" width="2.6" height="10" rx="1" fill="currentColor" />
      <rect x="9.4" y="3" width="2.6" height="10" rx="1" fill="currentColor" />
    </Glyph>
  );
}

/** Circular arrow — restart the retest / regenerate the goal. */
export function RestartIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M13.2 8a5.2 5.2 0 1 1-1.6-3.75"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M13.4 1.9v3h-3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Flag — conclude the retest with your own determination. */
export function FlagIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path d="M4 14.2V2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path
        d="M4 2.6h7.6l-1.6 2.9 1.6 2.9H4z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Open square with an exit arrow — end the session and tear the sandbox down. */
export function ExitIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M9.4 2.4H3.6a1.2 1.2 0 0 0-1.2 1.2v8.8a1.2 1.2 0 0 0 1.2 1.2h5.8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path
        d="M11.4 5.2 14 8l-2.6 2.8M13.6 8H6.6"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Paper plane — send a chat message to the agent. */
export function SendIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M14 2 2 6.6l4.8 1.9L8.7 14z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M14 2 6.8 8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </Glyph>
  );
}

/** Pencil — edit the goal in place. */
export function PencilIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M11.2 2.4a1.5 1.5 0 0 1 2.1 2.1l-7.3 7.3-2.8.7.7-2.8z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Terminal chevron + line — the docked sandbox shell. */
export function TerminalIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <rect
        x="1.6"
        y="2.6"
        width="12.8"
        height="10.8"
        rx="1.4"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path
        d="m4.4 6.2 2 1.9-2 1.9M8.6 10.2h3"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Crosshair — the retest's fixed target scope. */
export function TargetIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <circle cx="8" cy="8" r="5.4" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="8" r="1.6" fill="currentColor" />
      <path
        d="M8 .8v2.2M8 13v2.2M.8 8H3M13 8h2.2"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

/** Checklist — the operator-owned retest goal. */
export function GoalIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="m1.8 4.6 1.4 1.4 2.4-2.4M1.8 11.2l1.4 1.4 2.4-2.4"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M8.4 4.6h5.8M8.4 11.2h5.8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

/** Warning triangle — the agent paused and needs guidance. */
export function AlertIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path
        d="M8 2.2 14.6 13.4H1.4z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M8 6.4v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="11.4" r="0.85" fill="currentColor" />
    </Glyph>
  );
}

/** Gavel-ish seal — the recorded verdict. */
export function VerdictIcon({ size }: { size?: number }) {
  return (
    <Glyph size={size}>
      <circle cx="8" cy="6.6" r="4.4" stroke="currentColor" strokeWidth="1.3" />
      <path
        d="m6.1 6.6 1.4 1.4 2.6-2.6"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="m5.6 10.6-1 4.2 3.4-1.7 3.4 1.7-1-4.2"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}
