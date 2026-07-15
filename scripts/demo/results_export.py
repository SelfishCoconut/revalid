"""Demo for FR-12: export a complete run as one versioned, schema-valid JSON document.

Usage::

    uv run python scripts/demo/results_export.py

Runs fully offline: build a small run (finding -> approved plan -> verdict from a
mock retest), export the whole run via :func:`revalid.export.build_export`, then
validate the document against the *published* JSON schema
(``docs/reference/schemas/run-export.schema.json``) with ``jsonschema`` — proving
the FR-12 acceptance criterion the evaluation harness (FR-15) relies on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonschema import validate

from revalid.approval import approve_plan, execute_approved_plan, save_generated_plan
from revalid.db import IN_MEMORY, FindingRecord, create_db_engine, session_factory
from revalid.domain import Finding, Probe, RetestPlan, Severity
from revalid.export import build_export
from revalid.plan import PlanResult

_SCHEMA = Path(__file__).resolve().parents[2] / "docs/reference/schemas/run-export.schema.json"


def _plan() -> PlanResult:
    probe = Probe(
        kind="sqli-login-bypass",
        method="POST",
        url="http://localhost:3000/rest/user/login",
        json_body={"email": "' OR 1=1--", "password": "x"},
    )
    plan = RetestPlan(
        finding_title="SQLi login", actions=(probe,), raw={"finding_title": "SQLi login"}
    )
    return PlanResult(plan=plan)


def main() -> int:
    """Build a run, export it, and validate the document against the published schema."""
    session = session_factory(create_db_engine(IN_MEMORY))()
    session.add(FindingRecord.from_domain(Finding(title="SQLi login", severity=Severity.CRITICAL)))
    session.commit()
    save_generated_plan(session, 1, _plan())
    approve_plan(session, 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authentication": {"token": "t"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        execute_approved_plan(session, client, 1)

    export = build_export(session, generated_at=datetime(2026, 7, 15, tzinfo=UTC))
    document = export.model_dump(mode="json")
    print(
        f"1. exported run: schema_version={export.schema_version}, "
        f"{export.metrics.findings} finding(s), {export.metrics.plans} plan(s), "
        f"{export.metrics.verdicts} verdict(s) {dict(export.metrics.verdicts_by_status)}"
    )

    validate(instance=document, schema=json.loads(_SCHEMA.read_text()))
    print(f"2. document validates against the published schema ({_SCHEMA.name}) -- FR-12 AC met")
    print("3. first 400 chars of the export document:")
    print(json.dumps(document, indent=2)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
