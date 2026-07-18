"""Demo for FR-15: score a synthetic run against ground truth, print the metrics table.

Usage::

    uv run python scripts/demo/evaluate_run.py

Runs fully offline. Builds a synthetic FR-12 run export whose four findings land
in each scoring bucket — correct, a safe *inconclusive* hedge, a confidently
*wrong* verdict, and a correctly-inconclusive *ambiguous* case — then scores it
against a matching ground truth and prints the verdict-reliability table
(FR-15 / NFR-01). No lab, no LLM: the harness scores stored data.
"""

from __future__ import annotations

from datetime import UTC, datetime

from revalid.domain import Evidence, Finding, Severity, VerdictStatus
from revalid.eval import GroundTruth, GroundTruthEntry, evaluate, format_table
from revalid.export import (
    FindingExport,
    FindingVersionExport,
    Generator,
    RunExport,
    RunMetrics,
    VerdictExport,
)

_NOW = datetime(2026, 7, 15, tzinfo=UTC)


def _finding(fid: int, title: str) -> FindingExport:
    finding = Finding(title=title, severity=Severity.HIGH)
    return FindingExport(
        id=fid,
        report_id=None,
        version=1,
        finding=finding,
        versions=(
            FindingVersionExport(
                version=1,
                origin="extraction",
                edited_by=None,
                reason="",
                created_at=_NOW,
                finding=finding,
            ),
        ),
        notes=(),
    )


def _verdict(vid: int, fid: int, status: VerdictStatus, ms: float) -> VerdictExport:
    evidence = Evidence(
        request_method="POST",
        request_url="http://lab.local/probe",
        response_status=200,
        elapsed_ms=ms,
    )
    return VerdictExport(
        id=vid,
        finding_id=fid,
        probe_kind="demo",
        plan_id=None,
        plan_version=1,
        actor="executor",
        created_at=_NOW,
        source="batch",
        session_id=None,
        status=status,
        reason_code="demo",
        rationale="",
        matched_indicators=(),
        evidence=evidence,
    )


def _synthetic_export() -> RunExport:
    findings = (
        _finding(1, "SQL injection auth bypass in login"),
        _finding(2, "Broken access control on admin endpoint"),
        _finding(3, "Missing rate limiting on login"),
        _finding(4, "Sensitive data exposure via verbose error"),
    )
    verdicts = (
        _verdict(1, 1, VerdictStatus.STILL_OPEN, 120.0),  # matches expected -> correct
        _verdict(2, 2, VerdictStatus.FIXED, 90.0),  # expected still_open -> confidently WRONG
        _verdict(3, 3, VerdictStatus.INCONCLUSIVE, 75.0),  # expected still_open -> safe hedge
        _verdict(4, 4, VerdictStatus.INCONCLUSIVE, 60.0),  # ambiguous, inconclusive -> correct
    )
    metrics = RunMetrics(
        reports=0,
        findings=len(findings),
        plans=0,
        verdicts=len(verdicts),
        verdicts_by_status={"still_open": 1, "fixed": 1, "inconclusive": 2},
        total_elapsed_ms=345.0,
        mean_elapsed_ms=86.25,
    )
    return RunExport(
        schema_version="1.0",
        generated_at=_NOW,
        generator=Generator(tool="revalid", version="demo"),
        reports=(),
        findings=findings,
        plans=(),
        verdicts=verdicts,
        metrics=metrics,
    )


def _ground_truth() -> GroundTruth:
    return GroundTruth(
        target="synthetic demo target",
        source_report="synthetic",
        findings=(
            GroundTruthEntry(
                finding="SQL injection auth bypass in login", expected=VerdictStatus.STILL_OPEN
            ),
            GroundTruthEntry(
                finding="Broken access control on admin endpoint", expected=VerdictStatus.STILL_OPEN
            ),
            GroundTruthEntry(
                finding="Missing rate limiting on login", expected=VerdictStatus.STILL_OPEN
            ),
            GroundTruthEntry(
                finding="Sensitive data exposure via verbose error",
                expected=VerdictStatus.INCONCLUSIVE,
                ambiguous=True,
            ),
        ),
    )


def main() -> int:
    """Score the synthetic run and print the FR-15 metrics table."""
    report = evaluate(_synthetic_export(), _ground_truth())
    print(format_table(report))
    print()
    print(
        f"(demo shows all buckets; NFR-01 {'PASS' if report.nfr01_pass else 'FAIL'} here because "
        f"one finding is confidently wrong -> {report.correct_pct:.0%} correct)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
