import type { ReactElement } from "react";

import type { Theme } from "../lib/theme";

function MoonIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M13.5 9.3A5.5 5.5 0 0 1 6.7 2.5a5.5 5.5 0 1 0 6.8 6.8Z"
        fill="currentColor"
      />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="3.1" fill="currentColor" />
      <path
        d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2 3.1 3.1"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

const OPTIONS: { value: Theme; label: string; icon: () => ReactElement }[] = [
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "light", label: "Light", icon: SunIcon },
];

/** Segmented dark/light control. Dark is the default; the choice persists. */
export function ThemeToggle({
  theme,
  setTheme,
}: {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Theme"
      className="grid grid-cols-2 gap-1 rounded-lg border border-line bg-panel-2/50 p-1"
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            onClick={() => {
              setTheme(value);
            }}
            className={`flex items-center justify-center gap-1.5 rounded-md py-1.5 font-mono text-[11px] tracking-wide transition-colors ${
              active
                ? "bg-iris/15 text-iris-fg ring-1 ring-inset ring-iris/30"
                : "text-faint hover:text-dim"
            }`}
          >
            <Icon />
            {label}
          </button>
        );
      })}
    </div>
  );
}
