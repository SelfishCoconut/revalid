"""Unit tests for DefectDojo-style export mapping (FR-02). Pure, no I/O."""

import pytest

from revalid.domain import Severity
from revalid.ingest import IngestError, load_defectdojo_export, map_defectdojo_export


def _export(*findings: dict[str, object]) -> dict[str, object]:
    return {"findings": list(findings)}


def test_maps_all_core_fields() -> None:
    [finding] = map_defectdojo_export(
        _export(
            {
                "title": " SQL injection ",
                "severity": "Critical",
                "description": "boom",
                "steps_to_reproduce": "1. go\n\n2. inject\n",
                "endpoints": ["http://localhost:3000/rest/products/search"],
            }
        )
    )
    assert finding.title == "SQL injection"
    assert finding.severity is Severity.CRITICAL
    assert finding.description == "boom"
    assert finding.reproduction_steps == ("1. go", "2. inject")
    assert finding.affected_endpoints == ("http://localhost:3000/rest/products/search",)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Informational", Severity.INFO),
        ("info", Severity.INFO),
        ("LOW", Severity.LOW),
        ("Medium", Severity.MEDIUM),
        ("high", Severity.HIGH),
        (" Critical ", Severity.CRITICAL),
    ],
)
def test_severity_aliases(label: str, expected: Severity) -> None:
    [finding] = map_defectdojo_export(_export({"title": "t", "severity": label}))
    assert finding.severity is expected


def test_unknown_fields_preserved_in_raw() -> None:
    item: dict[str, object] = {"title": "t", "severity": "Low", "cwe": 89, "custom": {"a": 1}}
    [finding] = map_defectdojo_export(_export(item))
    assert finding.raw == item
    assert finding.raw["cwe"] == 89


def test_optional_fields_default_empty() -> None:
    [finding] = map_defectdojo_export(_export({"title": "t", "severity": "Info"}))
    assert finding.description == ""
    assert finding.affected_endpoints == ()
    assert finding.reproduction_steps == ()


def test_document_order_preserved() -> None:
    findings = map_defectdojo_export(
        _export(
            {"title": "first", "severity": "Low"},
            {"title": "second", "severity": "High"},
        )
    )
    assert [f.title for f in findings] == ["first", "second"]


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"findings": "nope"},
        42,
    ],
)
def test_invalid_document_shape_rejected(document: object) -> None:
    with pytest.raises(IngestError, match="findings"):
        map_defectdojo_export(document)


@pytest.mark.parametrize(
    ("item", "match"),
    [
        ("not-an-object", "expected an object"),
        ({"severity": "Low"}, "title"),
        ({"title": "  ", "severity": "Low"}, "title"),
        ({"title": "t"}, "severity"),
        ({"title": "t", "severity": "catastrophic"}, "severity"),
        ({"title": "t", "severity": "Low", "endpoints": "x"}, "endpoints"),
        ({"title": "t", "severity": "Low", "endpoints": [1]}, "endpoints"),
        ({"title": "t", "severity": "Low", "steps_to_reproduce": 3}, "steps_to_reproduce"),
    ],
)
def test_invalid_finding_rejected_with_index(item: object, match: str) -> None:
    with pytest.raises(IngestError, match=match) as excinfo:
        map_defectdojo_export(_export({"title": "ok", "severity": "Low"}, item))  # type: ignore[arg-type]
    assert "#1" in str(excinfo.value)


def test_load_rejects_invalid_json() -> None:
    with pytest.raises(IngestError, match="not valid JSON"):
        load_defectdojo_export("{nope")


def test_load_parses_json_string() -> None:
    [finding] = load_defectdojo_export('{"findings": [{"title": "t", "severity": "Low"}]}')
    assert finding.title == "t"


def test_stated_cvss_is_copied_verbatim_and_not_flagged_inferred() -> None:
    """A DefectDojo export that states a CVSS keeps it, with source provenance (#233)."""
    [finding] = map_defectdojo_export(
        _export(
            {
                "title": "SQLi",
                "severity": "Critical",
                "cvssv3": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                "cvssv3_score": 9.8,
            }
        )
    )
    assert finding.cvss.vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    assert finding.cvss.base_score == 9.8
    assert finding.cvss.inferred is False  # copied, not derived


def test_cvss_score_accepts_a_numeric_string() -> None:
    [finding] = map_defectdojo_export(
        _export({"title": "t", "severity": "Low", "cvssv3": "CVSS:3.1/AV:N", "cvssv3_score": "4.3"})
    )
    assert finding.cvss.base_score == 4.3


def test_unparsable_cvss_score_leaves_the_score_absent_not_zero() -> None:
    """A bad score must not become 0.0 — that would read as a real assessment."""
    [finding] = map_defectdojo_export(
        _export({"title": "t", "severity": "Low", "cvssv3": "CVSS:3.1/AV:N", "cvssv3_score": "n/a"})
    )
    assert finding.cvss.vector == "CVSS:3.1/AV:N"
    assert finding.cvss.base_score is None


def test_export_without_cvss_maps_to_an_empty_unflagged_code() -> None:
    [finding] = map_defectdojo_export(_export({"title": "t", "severity": "Low"}))
    assert finding.cvss.vector == ""
    assert finding.cvss.inferred is False  # nothing stated AND nothing derived


def test_stated_cwe_never_becomes_an_attack_technique() -> None:
    """CWE is a weakness id, not an ATT&CK technique — mapping one to the other is wrong."""
    [finding] = map_defectdojo_export(_export({"title": "t", "severity": "Low", "cwe": 89}))
    assert finding.mitre.techniques == ()
    assert finding.mitre.inferred is False
