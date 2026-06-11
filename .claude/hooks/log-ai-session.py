#!/usr/bin/env python3
"""SessionEnd hook: append a session record to docs/ai-usage/sessions/.

Feeds the audit trail required for the thesis AI-usage declaration
(Reglamento TFG 2026 §6): when AI was used and what it touched. The curated
per-session summary lives in docs/ai-usage/AI_USAGE_LOG.md; this file is the
raw, automatic record.
"""

import datetime
import json
import pathlib
import subprocess
import sys


def git(*args: str, cwd: str) -> str:
    """Run a git command, returning stdout or empty string on any failure."""
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def main() -> int:
    """Append a session record for the AI-usage audit trail."""
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd", ".")
    root = git("rev-parse", "--show-toplevel", cwd=cwd) or cwd

    now = datetime.datetime.now()
    out_dir = pathlib.Path(root) / "docs" / "ai-usage" / "sessions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{now:%Y-%m}.md"

    branch = git("branch", "--show-current", cwd=root) or "(no branch)"
    dirty = git("status", "--porcelain", cwd=root)
    dirty_files = "\n".join(f"  - `{line[3:]}`" for line in dirty.splitlines()) or "  - (none)"
    last_commits = git("log", "--oneline", "-5", "--since=12 hours ago", cwd=root)
    commits_md = "\n".join(f"  - {line}" for line in last_commits.splitlines()) or "  - (none)"

    entry = (
        f"\n## {now:%Y-%m-%d %H:%M} — session `{payload.get('session_id', 'unknown')[:8]}`\n\n"
        f"- Tool: Claude Code ({payload.get('reason', 'session end')})\n"
        f"- Branch: `{branch}`\n"
        f"- Uncommitted changes at session end:\n{dirty_files}\n"
        f"- Commits in the last 12h:\n{commits_md}\n"
    )
    if not out_file.exists():
        out_file.write_text(f"# AI sessions — {now:%Y-%m} (auto-generated, do not edit)\n")
    with out_file.open("a") as fh:
        fh.write(entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
