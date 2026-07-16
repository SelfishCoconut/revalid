#!/bin/sh
# PreToolUse hook (Bash): keep the Kanban honest (CLAUDE.md workflow, ADR-0004).
# When Claude creates a feature/fix branch or opens a PR, remind it that the
# change must trace to a GitHub issue and that the PR must say "Closes #<n>".
# Reads the hook JSON on stdin; emits additionalContext ONLY for matching
# commands (a no-op for every other Bash call).

cmd=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)

case "$cmd" in
  *"git switch -c "* | *"git checkout -b "* | *"gh pr create"*)
    python3 - <<'PY'
import json

msg = (
    "Kanban workflow (CLAUDE.md / ADR-0004): this change MUST trace to a GitHub "
    "issue on the board. If no issue exists yet, create one NOW — the "
    "feature-request skill, or `gh issue create` with a req:FR-xx / infra / thesis "
    "label + milestone — before the branch/PR. The PR body must contain "
    '"Closes #<n>" so board.yml advances the card (Backlog -> In Progress -> '
    "Verify). Do not open a feature PR without a linked issue."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": msg,
    }
}))
PY
    ;;
esac
exit 0
