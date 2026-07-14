import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "revalid-theme";

function initialTheme(): Theme {
  // The inline script in index.html has already set data-theme before paint.
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

/**
 * App theme, defaulting to dark. Writes `data-theme` on the root element (which
 * re-points every colour token) and persists the choice so it survives reloads.
 */
export function useTheme(): { theme: Theme; setTheme: (theme: Theme) => void } {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Private mode / storage disabled — the in-memory choice still applies.
    }
  }, [theme]);

  return { theme, setTheme };
}
