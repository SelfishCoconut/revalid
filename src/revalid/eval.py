"""Evaluation harness: score a run's verdicts against ground truth (FR-15 / NFR-01).

The harness consumes an FR-12 run export (:class:`revalid.export.RunExport`) and a
ground-truth file — one expected verdict per evaluation-set finding, plus an
``ambiguous`` flag marking findings whose only defensible outcome is
*inconclusive*. For each ground-truth finding it takes the system's **latest**
verdict from the export and classifies it:

- **correct** — the verdict matches the expected verdict.
- **inconclusive** — the system hedged (returned ``inconclusive``) where the
  truth was a definite verdict: a safe miss, not a confident error.
- **wrong** — the system returned a *confident* verdict that contradicts the
  truth. This is the dangerous case; per NFR-01 it counts double in the analysis,
  and on an ``ambiguous`` finding it violates the hard constraint.

:func:`evaluate` returns an :class:`EvalReport` with the per-finding rows, the
totals, timing, and the NFR-01 pass decision; :func:`format_table` renders the
metrics table for the thesis Results chapter. Nothing here touches the network —
scoring is a pure function of the export and the ground truth.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from revalid.domain import VerdictStatus
from revalid.export import RunExport, VerdictExport

# NFR-01 target: at least this fraction of evaluation-set findings must get the
# correct verdict.
NFR01_MIN_CORRECT = 0.70

# Sentinel written for `expected` in a generated ground-truth skeleton. Invalid as
# a VerdictStatus on purpose: an unfilled skeleton fails to load, so no run is ever
# scored against a placeholder (fail-closed authoring).
GROUND_TRUTH_TODO = "TODO"


class GroundTruthEntry(BaseModel):
    """The expected verdict for one evaluation-set finding (FR-15 ground truth).

    Attributes:
        finding: The finding title, matched to the export case-insensitively and
            whitespace-normalized (see :func:`normalize_title`).
        expected: The verdict a correct system should return. For an
            ``ambiguous`` finding this must be ``inconclusive``.
        ambiguous: Whether this finding's only defensible outcome is
            ``inconclusive`` (the NFR-01 hard-constraint cases).
        note: Free-text rationale for the expected verdict (kept in the report).
    """

    model_config = ConfigDict(frozen=True)

    finding: str
    expected: VerdictStatus
    ambiguous: bool = False
    note: str = ""


class GroundTruth(BaseModel):
    """The evaluation set's expected verdicts (FR-15).

    Attributes:
        target: The system under retest the verdicts are expected against
            (e.g. the pinned vulnerable Juice Shop version).
        source_report: Provenance of the pentest report the findings came from.
        findings: One expected verdict per evaluation-set finding.
    """

    model_config = ConfigDict(frozen=True)

    target: str
    source_report: str
    findings: tuple[GroundTruthEntry, ...]


class Classification(enum.StrEnum):
    """How a finding's actual verdict scored against its expected verdict."""

    CORRECT = "correct"
    INCONCLUSIVE = "inconclusive"
    WRONG = "wrong"
    NO_VERDICT = "no_verdict"


@dataclass(frozen=True)
class EvalRow:
    """One scored evaluation-set finding.

    Attributes:
        finding: The ground-truth finding title.
        expected: The expected verdict.
        actual: The system's latest verdict, or ``None`` if it was never
            retested in this run.
        ambiguous: Whether the finding is an NFR-01 hard-constraint case.
        classification: The scoring bucket this finding fell into.
        elapsed_ms: Round-trip time of the scored verdict's evidence.
    """

    finding: str
    expected: VerdictStatus
    actual: VerdictStatus | None
    ambiguous: bool
    classification: Classification
    elapsed_ms: float

    @property
    def confidently_wrong(self) -> bool:
        """True when the system gave a confident verdict that contradicts truth."""
        return self.classification is Classification.WRONG


@dataclass(frozen=True)
class EvalReport:
    """The scored evaluation run (FR-15 metrics table / NFR-01 decision).

    Attributes:
        rows: One scored row per matched ground-truth finding.
        unmatched_findings: Export finding titles with no ground-truth entry.
        unmatched_ground_truth: Ground-truth titles absent from the export.
    """

    rows: tuple[EvalRow, ...]
    unmatched_findings: tuple[str, ...] = ()
    unmatched_ground_truth: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """Number of scored findings."""
        return len(self.rows)

    def _count(self, classification: Classification) -> int:
        return sum(1 for row in self.rows if row.classification is classification)

    @property
    def correct(self) -> int:
        """Findings whose verdict matched the expected verdict."""
        return self._count(Classification.CORRECT)

    @property
    def inconclusive(self) -> int:
        """Findings where the system safely hedged (returned inconclusive)."""
        return self._count(Classification.INCONCLUSIVE)

    @property
    def wrong(self) -> int:
        """Findings given a confident verdict that contradicts the truth."""
        return self._count(Classification.WRONG)

    @property
    def no_verdict(self) -> int:
        """Ground-truth findings that were never retested in this run."""
        return self._count(Classification.NO_VERDICT)

    @property
    def correct_pct(self) -> float:
        """Fraction of scored findings that were correct (0.0 when none)."""
        return self.correct / self.total if self.total else 0.0

    @property
    def confidently_wrong(self) -> int:
        """Count of confident, contradicting verdicts (NFR-01 counts these double)."""
        return self.wrong

    @property
    def weighted_error(self) -> int:
        """Confident errors weighted double, per the NFR-01 analysis rule."""
        return 2 * self.wrong

    @property
    def wrong_on_ambiguous(self) -> int:
        """Confident verdicts on ambiguous findings — the NFR-01 hard-constraint breaches."""
        return sum(1 for row in self.rows if row.ambiguous and row.confidently_wrong)

    @property
    def total_elapsed_ms(self) -> float:
        """Total round-trip time across the scored verdicts."""
        return sum(row.elapsed_ms for row in self.rows)

    @property
    def mean_elapsed_ms(self) -> float:
        """Mean round-trip time across the scored verdicts (0.0 when none)."""
        return self.total_elapsed_ms / self.total if self.total else 0.0

    @property
    def nfr01_pass(self) -> bool:
        """NFR-01: ≥70% correct AND no ambiguous finding given a confident verdict."""
        return self.correct_pct >= NFR01_MIN_CORRECT and self.wrong_on_ambiguous == 0


def classify(
    expected: VerdictStatus, actual: VerdictStatus | None, *, ambiguous: bool
) -> Classification:
    """Score one finding's actual verdict against its expected verdict (NFR-01).

    Args:
        expected: The verdict a correct system should return.
        actual: The system's verdict, or ``None`` if the finding was not retested.
        ambiguous: Whether the finding's only defensible outcome is inconclusive
            (unused in the logic — an ambiguous entry simply has ``expected`` =
            ``inconclusive`` — but named for call-site clarity).

    Returns:
        The scoring bucket: ``NO_VERDICT`` when nothing ran, ``CORRECT`` on a
        match, ``INCONCLUSIVE`` when the system safely hedged, else ``WRONG``.
    """
    del ambiguous  # expressed through `expected`; kept for call-site readability
    if actual is None:
        return Classification.NO_VERDICT
    if actual == expected:
        return Classification.CORRECT
    if actual is VerdictStatus.INCONCLUSIVE:
        return Classification.INCONCLUSIVE
    return Classification.WRONG


def normalize_title(title: str) -> str:
    """Normalize a finding title to its match key (lowercased, whitespace-collapsed).

    The single key both scoring (:func:`evaluate`) and the ground-truth authoring
    aid (:func:`ground_truth_skeleton`) use to line findings up, so they can never
    disagree on what counts as the same finding.
    """
    return " ".join(title.lower().split())


def latest_verdict_by_finding(export: RunExport) -> dict[int, VerdictExport]:
    """Map each finding id to its latest verdict in the export (highest verdict id)."""
    latest: dict[int, VerdictExport] = {}
    for verdict in export.verdicts:
        current = latest.get(verdict.finding_id)
        if current is None or verdict.id > current.id:
            latest[verdict.finding_id] = verdict
    return latest


def evaluate(export: RunExport, ground_truth: GroundTruth) -> EvalReport:
    """Score every ground-truth finding against the run export (FR-15).

    Matches ground-truth entries to export findings by normalized title, takes
    each finding's latest verdict, and classifies it. Findings present on only
    one side are surfaced (``unmatched_*``) rather than silently dropped — an
    unmatched entry means the ground truth and the run disagree on the finding
    set and the score would otherwise be quietly wrong.

    Args:
        export: The FR-12 run export to score.
        ground_truth: The evaluation set's expected verdicts.

    Returns:
        The scored :class:`EvalReport`.
    """
    findings_by_key = {normalize_title(f.finding.title): f for f in export.findings}
    latest = latest_verdict_by_finding(export)
    matched_keys: set[str] = set()
    rows: list[EvalRow] = []
    unmatched_gt: list[str] = []

    for entry in ground_truth.findings:
        key = normalize_title(entry.finding)
        finding = findings_by_key.get(key)
        if finding is None:
            unmatched_gt.append(entry.finding)
            continue
        matched_keys.add(key)
        verdict = latest.get(finding.id)
        actual = verdict.verdict.status if verdict is not None else None
        rows.append(
            EvalRow(
                finding=entry.finding,
                expected=entry.expected,
                actual=actual,
                ambiguous=entry.ambiguous,
                classification=classify(entry.expected, actual, ambiguous=entry.ambiguous),
                elapsed_ms=verdict.verdict.evidence.elapsed_ms if verdict is not None else 0.0,
            )
        )

    unmatched_findings = tuple(
        f.finding.title for key, f in findings_by_key.items() if key not in matched_keys
    )
    return EvalReport(
        rows=tuple(rows),
        unmatched_findings=unmatched_findings,
        unmatched_ground_truth=tuple(unmatched_gt),
    )


def load_ground_truth(path: Path) -> GroundTruth:
    """Load and validate a ground-truth file (FR-15)."""
    return GroundTruth.model_validate_json(path.read_text())


def load_export(path: Path) -> RunExport:
    """Load and validate an FR-12 run export from disk."""
    return RunExport.model_validate(json.loads(path.read_text()))


def ground_truth_skeleton(export: RunExport) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Build a fill-in-the-blanks ground-truth skeleton from a run export (FR-15 aid).

    Emits one entry per export finding, its ``finding`` title already keyed so it
    matches the run exactly, with ``expected`` set to :data:`GROUND_TRUTH_TODO` for
    the author to replace. Because that sentinel is not a valid verdict, an
    unfilled skeleton won't load — the author cannot accidentally score a run
    against placeholders.

    Args:
        export: The FR-12 run export to seed the ground truth from.

    Returns:
        A ``(skeleton, duplicate_titles)`` pair. ``skeleton`` is JSON-serializable
        and shaped like :class:`GroundTruth`; ``duplicate_titles`` lists finding
        titles that collapse to the same match key (they would collide in scoring,
        so the author must disambiguate them).
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    entries: list[dict[str, Any]] = []
    for finding in export.findings:
        title = finding.finding.title
        key = normalize_title(title)
        if key in seen:
            duplicates.append(title)
        seen.add(key)
        entries.append(
            {"finding": title, "expected": GROUND_TRUTH_TODO, "ambiguous": False, "note": ""}
        )
    skeleton = {
        "target": "<pin the evaluated target, e.g. OWASP Juice Shop v17.1.1>",
        "source_report": export.reports[0].filename if export.reports else "<pentest report>",
        "findings": entries,
    }
    return skeleton, tuple(duplicates)


_STATUS_LABEL = {
    VerdictStatus.STILL_OPEN: "still_open",
    VerdictStatus.FIXED: "fixed",
    VerdictStatus.INCONCLUSIVE: "inconclusive",
    None: "—",
}


def _row_line(row: EvalRow) -> str:
    actual = _STATUS_LABEL[row.actual]
    flag = " (ambiguous)" if row.ambiguous else ""
    mark = {
        Classification.CORRECT: "OK ",
        Classification.INCONCLUSIVE: "~  ",
        Classification.WRONG: "XX ",
        Classification.NO_VERDICT: "-- ",
    }[row.classification]
    title = row.finding if len(row.finding) <= 44 else row.finding[:41] + "..."
    return f"  {mark} {title:<44} expected={row.expected.value:<12} actual={actual:<12}{flag}"


def format_table(report: EvalReport) -> str:
    """Render the FR-15 metrics table (correct / wrong / inconclusive, totals, timing)."""
    lines = ["Evaluation — verdict reliability (FR-15 / NFR-01)", ""]
    lines.extend(_row_line(row) for row in report.rows)
    lines.append("")
    lines.append(
        f"  total={report.total}  correct={report.correct}  "
        f"inconclusive={report.inconclusive}  wrong={report.wrong}"
        + (f"  no_verdict={report.no_verdict}" if report.no_verdict else "")
    )
    lines.append(
        f"  correct={report.correct_pct:.0%}  confidently_wrong={report.confidently_wrong} "
        f"(weighted x2 = {report.weighted_error})  wrong_on_ambiguous={report.wrong_on_ambiguous}"
    )
    lines.append(
        f"  timing: total={report.total_elapsed_ms:.0f}ms  mean={report.mean_elapsed_ms:.0f}ms"
    )
    if report.unmatched_ground_truth:
        lines.append(
            f"  ! ground-truth findings not in the run: {list(report.unmatched_ground_truth)}"
        )
    if report.unmatched_findings:
        lines.append(f"  ! run findings not in the ground truth: {list(report.unmatched_findings)}")
    verdict = "PASS" if report.nfr01_pass else "FAIL"
    lines.append("")
    lines.append(
        f"  NFR-01: {verdict}  (need ≥{NFR01_MIN_CORRECT:.0%} correct and zero "
        f"confident verdicts on ambiguous findings)"
    )
    return "\n".join(lines)
