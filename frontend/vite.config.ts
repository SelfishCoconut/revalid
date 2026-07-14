import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The whole tool runs from one uvicorn process: FastAPI serves the built SPA
// at `/` and the JSON API lives under `/api`. In dev, proxy `/api` to the
// backend so the SPA can use same-origin relative paths everywhere.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text-summary", "text"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
        "src/main.tsx",
        "src/vite-env.d.ts",
      ],
      thresholds: {
        // Whole-app regression floor: keep total coverage from eroding below
        // roughly today's level as untested UI grows. Raise as suites land.
        statements: 45,
        branches: 40,
        functions: 38,
        lines: 45,
        // Modules this test-health pass owns stay fully covered — the pure
        // selectors/formatters, the status colour table, and the shared
        // Badge/Button/track/meter primitives.
        "**/lib/selectors.ts": { statements: 100, branches: 100, functions: 100, lines: 100 },
        "**/lib/format.ts": { statements: 100, branches: 100, functions: 100, lines: 100 },
        "**/lib/status.ts": { statements: 100, branches: 100, functions: 100, lines: 100 },
        "**/components/ui/Badge.tsx": { statements: 100, branches: 100, functions: 100, lines: 100 },
        "**/components/ui/Button.tsx": { statements: 100, branches: 100, functions: 100, lines: 100 },
        "**/components/PipelineTrack.tsx": { statements: 100, branches: 100, functions: 100, lines: 100 },
        "**/components/DeterminationMeter.tsx": { statements: 100, branches: 100, functions: 100, lines: 100 },
      },
    },
  },
});
