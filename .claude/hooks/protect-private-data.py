#!/usr/bin/env python3
"""PreToolUse hook: block AI access to quarantined sensitive data.

Enforces the data-protection clause of the ESII TFG regulation (2026, §6):
no personal or protected third-party data may enter AI context. Anything
under data/private/ plus env/credential/key files is off-limits.

Exit code 2 = block the tool call (stderr is shown to Claude).
"""

import json
import re
import sys

BLOCKED = re.compile(
    r"data/private|\.env\b|credentials|\.pem\b|\.key\b",
    re.IGNORECASE,
)


def main() -> int:
    """Inspect the hook payload; exit 2 to block access to quarantined paths."""
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})
    # Read/Grep/Glob carry paths; Bash carries a command string. File *content*
    # is deliberately not inspected: writing ABOUT credentials (docs, rules)
    # is fine — the policy targets accessing sensitive paths.
    haystack = " ".join(
        str(tool_input.get(k, "")) for k in ("file_path", "path", "pattern", "command")
    )
    if BLOCKED.search(haystack):
        print(
            "BLOCKED by data-protection policy (Reglamento TFG 2026 §6): "
            "data/private/, .env, credential and key files must never enter "
            "AI context. Use synthetic data in tests/data/ instead.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
