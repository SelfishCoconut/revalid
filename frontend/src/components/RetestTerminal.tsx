import { useEffect, useRef } from "react";

import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

/**
 * A thin, read-only `xterm.js` wrapper that plays back a retest session's
 * transcript. `lines` grows monotonically as the parent derives more of it
 * from the session's event log; only the lines written since the last
 * render are appended, so the buffer is never rebuilt from scratch.
 *
 * jsdom (used by the test suite) lacks the canvas/DOM APIs xterm needs, so
 * construction, `open`, and `writeln` can all throw there. Each is guarded
 * with try/catch so the component still renders its host div and never
 * crashes a test — only the terminal's own rendering is skipped.
 */
export function RetestTerminal({ lines }: { lines: string[] }) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const written = useRef(0);

  useEffect(() => {
    if (!host.current || term.current) return;
    try {
      const instance = new Terminal({
        convertEol: true,
        // xterm renders to a canvas and needs a literal font stack — a CSS
        // `var(--font-mono)` never resolves here and silently falls back to the
        // browser's default monospace. Spell out IBM Plex Mono to match the app.
        fontFamily: '"IBM Plex Mono", ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace',
        fontSize: 12.5,
        lineHeight: 1.35,
        disableStdin: true,
        theme: {
          background: "#0c101a",
          foreground: "#dbe3ee",
          cursor: "#8c7bff",
          cursorAccent: "#0c101a",
          selectionBackground: "rgba(140, 123, 255, 0.35)",
        },
      });
      instance.open(host.current);
      term.current = instance;
    } catch {
      // jsdom-safe: no canvas/DOM APIs available — the host div still mounts.
    }

    return () => {
      try {
        term.current?.dispose();
      } catch {
        // jsdom-safe: dispose can throw on a terminal that never fully opened.
      }
      term.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = term.current;
    if (!instance) return;
    for (let i = written.current; i < lines.length; i++) {
      try {
        instance.writeln(lines[i]);
      } catch {
        // jsdom-safe: writing can throw on a terminal that never fully opened.
      }
    }
    written.current = lines.length;
  }, [lines]);

  return (
    <div ref={host} data-testid="retest-terminal" className="h-52 overflow-hidden rounded-md" />
  );
}
