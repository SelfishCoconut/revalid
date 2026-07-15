"""Unit tests for the FR-15 evaluation harness / NFR-01 scoring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from revalid.domain import Evidence, Finding, Severity, Verdict, VerdictStatus
from revalid.eval import (
    GROUND_TRUTH_TODO,
    Classification,
    GroundTruth,
    GroundTruthEntry,
    classify,
    evaluate,
    format_table,
    ground_truth_skeleton,
    latest_verdict_by_finding,
    load_export,
    load_ground_truth,
    normalize_title,
)
from revalid.export import (
    FindingExport,
    Generator,
    ReportExport,
    RunExport,
    RunMetrics,
    VerdictExport,
)

_NOW = datetime(2026, 7, 15, tzinfo=UTC)
_EXAMPLE_GT = Path(__file__).resolve().parents[2] / "tests/data/eval/ground_truth.example.json"

_S = VerdictStatus.STILL_OPEN
_F = VerdictStatus.FIXED
_I = VerdictStatus.INCONCLUSIVE


def _finding(fid: int, title: str) -> FindingExport:
    return FindingExport(
        id=fid, report_id=None, finding=Finding(title=title, severity=Severity.HIGH)
    )


def _verdict(vid: int, fid: int, status: VerdictStatus, ms: float = 10.0) -> VerdictExport:
    evidence = Evidence(
        request_method="GET", request_url="http://x/p", response_status=200, elapsed_ms=ms
    )
    return VerdictExport(
        id=vid,
        finding_id=fid,
        probe_kind="demo",
        plan_id=None,
        plan_version=1,
        actor="executor",
        created_at=_NOW,
        verdict=Verdict(status=status, reason_code="demo", evidence=evidence),
    )


def _export(findings: tuple[FindingExport, ...], verdicts: tuple[VerdictExport, ...]) -> RunExport:
    metrics = RunMetrics(
        reports=0,
        findings=len(findings),
        plans=0,
        verdicts=len(verdicts),
        verdicts_by_status={"still_open": 0, "fixed": 0, "inconclusive": 0},
        total_elapsed_ms=0.0,
        mean_elapsed_ms=0.0,
    )
    return RunExport(
        schema_version="1.0",
        generated_at=_NOW,
        generator=Generator(tool="revalid", version="test"),
        reports=(),
        findings=findings,
        plans=(),
        verdicts=verdicts,
        metrics=metrics,
    )


def _gt(*entries: GroundTruthEntry) -> GroundTruth:
    return GroundTruth(target="t", source_report="s", findings=entries)


@pytest.mark.parametrize(
    ("expected", "actual", "ambiguous", "want"),
    [
        (_S, _S, False, Classification.CORRECT),
        (_I, _I, True, Classification.CORRECT),
        (_S, _I, False, Classification.INCONCLUSIVE),  # safe hedge
        (_S, _F, False, Classification.WRONG),  # confidently wrong
        (_I, _S, True, Classification.WRONG),  # confident verdict on an ambiguous finding
        (_S, None, False, Classification.NO_VERDICT),  # never retested
    ],
)
def test_classify(
    expected: VerdictStatus, actual: VerdictStatus | None, ambiguous: bool, want: Classification
) -> None:
    assert classify(expected, actual, ambiguous=ambiguous) is want


def test_latest_verdict_wins() -> None:
    export = _export((_finding(1, "a"),), (_verdict(1, 1, _F), _verdict(2, 1, _S)))
    latest = latest_verdict_by_finding(export)
    assert latest[1].id == 2
    assert latest[1].verdict.status is _S


def test_latest_verdict_keeps_highest_id_regardless_of_order() -> None:
    # Higher-id verdict listed first: a later, lower-id verdict must not replace it.
    export = _export((_finding(1, "a"),), (_verdict(9, 1, _S), _verdict(2, 1, _F)))
    assert latest_verdict_by_finding(export)[1].id == 9


def test_load_export_round_trips(tmp_path: Path) -> None:
    findings = (_finding(1, "SQLi login"),)
    export = _export(findings, (_verdict(1, 1, _S),))
    path = tmp_path / "run-export.json"
    path.write_text(export.model_dump_json())
    loaded = load_export(path)
    assert loaded.findings[0].finding.title == "SQLi login"
    assert loaded.verdicts[0].verdict.status is _S


def test_evaluate_buckets_and_nfr01_pass() -> None:
    findings = (_finding(1, "SQLi login"), _finding(2, "XSS ambiguous"), _finding(3, "Headers"))
    verdicts = (_verdict(1, 1, _S, 100.0), _verdict(2, 2, _I, 50.0), _verdict(3, 3, _S, 30.0))
    gt = _gt(
        GroundTruthEntry(finding="SQLi login", expected=_S),
        GroundTruthEntry(finding="XSS ambiguous", expected=_I, ambiguous=True),
        GroundTruthEntry(finding="Headers", expected=_S),
    )
    report = evaluate(_export(findings, verdicts), gt)

    assert (report.total, report.correct, report.wrong, report.inconclusive) == (3, 3, 0, 0)
    assert report.correct_pct == 1.0
    assert report.confidently_wrong == 0
    assert report.wrong_on_ambiguous == 0
    assert report.nfr01_pass is True
    assert report.total_elapsed_ms == 180.0
    assert report.mean_elapsed_ms == 60.0


def test_evaluate_confidently_wrong_on_ambiguous_fails_nfr01() -> None:
    findings = (_finding(1, "SQLi login"), _finding(2, "Ambiguous case"))
    # Ambiguous finding gets a confident (still_open) verdict -> hard-constraint breach.
    verdicts = (_verdict(1, 1, _S), _verdict(2, 2, _S))
    gt = _gt(
        GroundTruthEntry(finding="SQLi login", expected=_S),
        GroundTruthEntry(finding="Ambiguous case", expected=_I, ambiguous=True),
    )
    report = evaluate(_export(findings, verdicts), gt)

    assert report.correct == 1 and report.wrong == 1
    assert report.correct_pct == 0.5
    assert report.confidently_wrong == 1
    assert report.weighted_error == 2
    assert report.wrong_on_ambiguous == 1
    assert report.nfr01_pass is False  # both: <70% correct AND breached the hard constraint


def test_evaluate_below_threshold_fails_even_without_ambiguity_breach() -> None:
    findings = tuple(_finding(i, f"f{i}") for i in range(1, 4))
    # 1 correct, 2 safe hedges -> 33% correct, no confident errors -> still a FAIL on the ratio.
    verdicts = (_verdict(1, 1, _S), _verdict(2, 2, _I), _verdict(3, 3, _I))
    gt = _gt(*(GroundTruthEntry(finding=f"f{i}", expected=_S) for i in range(1, 4)))
    report = evaluate(_export(findings, verdicts), gt)

    assert report.wrong == 0 and report.wrong_on_ambiguous == 0
    assert report.correct_pct < 0.70
    assert report.nfr01_pass is False


def test_evaluate_matches_by_normalized_title() -> None:
    findings = (_finding(1, "  SQL  Injection  LOGIN "),)
    verdicts = (_verdict(1, 1, _S),)
    gt = _gt(GroundTruthEntry(finding="sql injection login", expected=_S))
    report = evaluate(_export(findings, verdicts), gt)
    assert report.correct == 1
    assert report.unmatched_findings == () and report.unmatched_ground_truth == ()


def test_evaluate_surfaces_unmatched_on_both_sides() -> None:
    findings = (_finding(1, "in the run only"),)
    verdicts = (_verdict(1, 1, _S),)
    gt = _gt(GroundTruthEntry(finding="in the ground truth only", expected=_S))
    report = evaluate(_export(findings, verdicts), gt)
    assert report.total == 0
    assert report.unmatched_findings == ("in the run only",)
    assert report.unmatched_ground_truth == ("in the ground truth only",)


def test_evaluate_no_verdict_when_finding_not_retested() -> None:
    findings = (_finding(1, "never retested"),)
    gt = _gt(GroundTruthEntry(finding="never retested", expected=_S))
    report = evaluate(_export(findings, ()), gt)
    assert report.no_verdict == 1
    assert report.rows[0].classification is Classification.NO_VERDICT
    assert report.rows[0].actual is None


def test_format_table_reports_pass_and_totals() -> None:
    findings = (_finding(1, "SQLi login"),)
    verdicts = (_verdict(1, 1, _S),)
    gt = _gt(GroundTruthEntry(finding="SQLi login", expected=_S))
    table = format_table(evaluate(_export(findings, verdicts), gt))
    assert "NFR-01: PASS" in table
    assert "total=1" in table and "correct=100%" in table


def test_format_table_flags_unmatched() -> None:
    findings = (_finding(1, "run only"),)
    gt = _gt(GroundTruthEntry(finding="gt only", expected=_S))
    table = format_table(evaluate(_export(findings, ()), gt))
    assert "ground-truth findings not in the run" in table
    assert "run findings not in the ground truth" in table


def test_example_ground_truth_file_is_valid() -> None:
    gt = load_ground_truth(_EXAMPLE_GT)
    assert len(gt.findings) >= 1
    # The example includes an ambiguous hard-constraint case with expected=inconclusive.
    ambiguous = [f for f in gt.findings if f.ambiguous]
    assert ambiguous and all(f.expected is VerdictStatus.INCONCLUSIVE for f in ambiguous)


@pytest.mark.parametrize(
    ("raw", "want"),
    [("  SQL  Injection ", "sql injection"), ("Fixed", "fixed"), ("A\tB\nC", "a b c")],
)
def test_normalize_title(raw: str, want: str) -> None:
    assert normalize_title(raw) == want


def _export_with_reports(
    findings: tuple[FindingExport, ...], reports: tuple[ReportExport, ...]
) -> RunExport:
    export = _export(findings, ())
    return export.model_copy(update={"reports": reports})


def test_ground_truth_skeleton_one_todo_entry_per_finding() -> None:
    findings = (_finding(1, "SQLi login"), _finding(2, "Broken access control"))
    skeleton, duplicates = ground_truth_skeleton(_export(findings, ()))

    assert duplicates == ()
    assert [e["finding"] for e in skeleton["findings"]] == ["SQLi login", "Broken access control"]
    assert all(e["expected"] == GROUND_TRUTH_TODO for e in skeleton["findings"])
    assert all(e["ambiguous"] is False for e in skeleton["findings"])
    assert skeleton["source_report"] == "<pentest report>"  # no report in the export


def test_ground_truth_skeleton_pulls_source_report_from_first_report() -> None:
    report = ReportExport(
        id=1,
        filename="pentest.pdf",
        status="ready",
        model="ollama:x",
        finding_count=1,
        created_at=_NOW,
    )
    skeleton, _ = ground_truth_skeleton(_export_with_reports((_finding(1, "a"),), (report,)))
    assert skeleton["source_report"] == "pentest.pdf"


def test_ground_truth_skeleton_flags_colliding_titles() -> None:
    findings = (_finding(1, "SQLi Login"), _finding(2, "sqli  login"), _finding(3, "Other"))
    _, duplicates = ground_truth_skeleton(_export(findings, ()))
    assert duplicates == ("sqli  login",)


def test_unfilled_skeleton_does_not_validate_but_filled_one_does() -> None:
    skeleton, _ = ground_truth_skeleton(_export((_finding(1, "SQLi login"),), ()))
    # Fail-closed: the TODO sentinel is not a valid verdict, so an unfilled
    # skeleton cannot be loaded and scored by accident.
    with pytest.raises(ValidationError):
        GroundTruth.model_validate(skeleton)
    # Once the author fills in a real verdict, it validates.
    skeleton["findings"][0]["expected"] = "still_open"
    assert GroundTruth.model_validate(skeleton).findings[0].expected is VerdictStatus.STILL_OPEN
