"""Demo for FR-12: export a complete run as one versioned, schema-valid JSON document.

Usage::

    uv run python scripts/demo/results_export.py

Runs fully offline: build a small run (finding -> agentic retest session -> recorded
verdict), export the whole run via :func:`revalid.export.build_export`, then validate
the document against the *published* JSON schema
(``docs/reference/schemas/run-export.schema.json``) with ``jsonschema`` — proving
the FR-12 acceptance criterion the evaluation harness (FR-15) relies on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import validate

from revalid import retest_session as rs
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import Finding, SessionEventKind, Severity, VerdictStatus
from revalid.export import build_export
from revalid.findings import create_finding

_SCHEMA = Path(__file__).resolve().parents[2] / "docs/reference/schemas/run-export.schema.json"


def main() -> int:
    """Build a run, export it, and validate the document against the published schema."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    create_finding(session, Finding(title="SQLi login", severity=Severity.CRITICAL))
    session.commit()

    sid = rs.create_session(session, finding_id=1, model="demo").id
    rs.append_event(
        session,
        sid,
        SessionEventKind.COMMAND_OUTPUT,
        {
            "command": "curl -s http://localhost:3000/rest/user/login",
            "stdout": '{"authentication": {"token": "t"}}',
            "stderr": "",
            "exit_code": 0,
            "elapsed_ms": 12,
        },
    )
    rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")

    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))
    document = export.model_dump(mode="json")
    print(
        f"1. exported run: schema_version={export.schema_version}, "
        f"{export.metrics.findings} finding(s), "
        f"{export.metrics.verdicts} verdict(s) {dict(export.metrics.verdicts_by_status)}"
    )

    validate(instance=document, schema=json.loads(_SCHEMA.read_text()))
    print(f"2. document validates against the published schema ({_SCHEMA.name}) -- FR-12 AC met")
    print("3. first 400 chars of the export document:")
    print(json.dumps(document, indent=2)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
