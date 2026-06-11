#!/bin/sh
# PostToolUse hook (Edit|Write): auto-format touched Python files with ruff.
# Reads the hook JSON on stdin, extracts .tool_input.file_path.

file_path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')

case "$file_path" in
  */tfg/*.py)
    cd "$(dirname "$0")/../.." || exit 0
    uv run ruff format --quiet "$file_path" 2>/dev/null
    uv run ruff check --fix --quiet "$file_path" 2>/dev/null
    ;;
esac
exit 0
