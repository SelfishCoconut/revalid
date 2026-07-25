import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    rules: {
      ...reactHooks.configs["recommended-latest"].rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      // A size ceiling, so the SPA has one at all (issue #253). The backend is held to
      // xenon --max-absolute C and "refactor, don't suppress" (CLAUDE.md), while the
      // most-reshaped file here — the retest console, eight ADRs in nine days — grew to
      // a single ~900-line component with nothing to stop it. This is deliberately set
      // just above that file rather than at a virtuous number: it freezes the worst case
      // instead of demanding an immediate refactor of a screen that works and has been
      // validated. Lowering it is the point of #253; raising it needs a reason.
      "max-lines-per-function": [
        "error",
        { max: 950, skipBlankLines: false, skipComments: false },
      ],
    },
  },
  {
    // Test files and helpers legitimately export non-component utilities.
    files: ["**/*.test.{ts,tsx}", "src/test/**/*.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
      // Test suites are long by nature: one describe block per module, many cases.
      "max-lines-per-function": "off",
    },
  },
);
