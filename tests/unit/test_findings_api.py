"""Unit tests for the FR-16 finding revision & annotation endpoints (ADR-0024).

TestClient over in-memory SQLite. A finding is seeded directly on the shared
engine (extraction is exercised elsewhere), then the versioned-edit and
stage-tagged-notes routes are driven over HTTP.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import CvssCode, Finding, MitreMapping, Severity
from revalid.findings import create_finding


def _client_with_finding() -> tuple[TestClient, int]:
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as session:
        record = create_finding(
            session,
            Finding(
                title="SQLi login",
                severity=Severity.HIGH,
                description="original",
                raw={"model": "test", "source": "extraction"},
            ),
            report_id=3,
        )
        session.commit()
        finding_id = record.id
    return TestClient(create_app(engine=engine)), finding_id


def test_list_findings_returns_the_current_version() -> None:
    client, finding_id = _client_with_finding()

    client.post(
        f"/api/findings/{finding_id}",
        json={"title": "SQLi login (edited)", "severity": "critical", "description": "sharper"},
    )

    listed = client.get("/api/findings").json()
    assert len(listed) == 1
    assert listed[0]["id"] == finding_id
    assert listed[0]["version"] == 2
    assert listed[0]["title"] == "SQLi login (edited)"
    assert listed[0]["severity"] == "critical"


def test_edit_finding_appends_a_version_and_keeps_history() -> None:
    client, finding_id = _client_with_finding()

    edited = client.post(
        f"/api/findings/{finding_id}",
        json={
            "title": "SQLi login",
            "severity": "high",
            "description": "corrected",
            "reason": "wrong endpoint",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert edited.json()["description"] == "corrected"

    versions = client.get(f"/api/findings/{finding_id}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["origin"] == "extraction"
    assert versions[0]["description"] == "original"
    assert versions[1]["origin"] == "edit"
    assert versions[1]["reason"] == "wrong endpoint"


def test_edit_carries_forward_extraction_lineage() -> None:
    client, finding_id = _client_with_finding()

    client.post(
        f"/api/findings/{finding_id}",
        json={"title": "SQLi login", "severity": "high", "description": "corrected"},
    )
    versions = client.get(f"/api/findings/{finding_id}/versions").json()
    # The edit did not discard how the finding was originally produced (FR-10).
    assert versions[1]["raw"] == {"model": "test", "source": "extraction"}


def test_edit_finding_404_for_missing_finding() -> None:
    client, _ = _client_with_finding()
    resp = client.post("/api/findings/999", json={"title": "x", "severity": "low"})
    assert resp.status_code == 404


def test_versions_404_for_missing_finding() -> None:
    client, _ = _client_with_finding()
    assert client.get("/api/findings/999/versions").status_code == 404


def test_add_note_and_list_newest_first() -> None:
    client, finding_id = _client_with_finding()

    first = client.post(
        f"/api/findings/{finding_id}/notes", json={"stage": "plan", "body": "check /admin too"}
    )
    assert first.status_code == 201
    assert first.json()["stage"] == "plan"
    client.post(
        f"/api/findings/{finding_id}/notes", json={"stage": "verdict", "body": "still open"}
    )

    notes = client.get(f"/api/findings/{finding_id}/notes").json()
    assert [n["body"] for n in notes] == ["still open", "check /admin too"]
    assert notes[0]["stage"] == "verdict"


def test_note_defaults_to_general_stage() -> None:
    client, finding_id = _client_with_finding()
    resp = client.post(f"/api/findings/{finding_id}/notes", json={"body": "overview note"})
    assert resp.status_code == 201
    assert resp.json()["stage"] == "general"


def test_notes_404_for_missing_finding() -> None:
    client, _ = _client_with_finding()
    assert client.get("/api/findings/999/notes").status_code == 404
    assert client.post("/api/findings/999/notes", json={"body": "x"}).status_code == 404


def _client_with_scored_finding() -> tuple[TestClient, int]:
    """Seed a finding that already carries a model-*inferred* CVSS/ATT&CK (FR-19)."""
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as session:
        record = create_finding(
            session,
            Finding(
                title="SQLi login",
                severity=Severity.HIGH,
                cvss=CvssCode(vector="CVSS:3.1/AV:N/AC:L", base_score=9.8, inferred=True),
                mitre=MitreMapping(techniques=("T1190",), inferred=True),
            ),
            report_id=3,
        )
        session.commit()
        finding_id = record.id
    return TestClient(create_app(engine=engine)), finding_id


def test_edit_without_taxonomy_keeps_the_existing_cvss_and_mitre() -> None:
    """Editing anything else must not wipe the taxonomy (FR-19 regression, #226).

    ``FindingEditIn.to_finding`` did not carry ``cvss``/``mitre`` across, so they
    fell back to their empty defaults and every operator edit silently destroyed
    the values extraction had derived.
    """
    client, finding_id = _client_with_scored_finding()

    edited = client.post(
        f"/api/findings/{finding_id}",
        json={"title": "SQLi login", "severity": "high", "description": "corrected"},
    ).json()

    assert edited["cvss"]["vector"] == "CVSS:3.1/AV:N/AC:L"
    assert edited["cvss"]["base_score"] == 9.8
    assert edited["mitre"]["techniques"] == ["T1190"]
    # Provenance survives too: an untouched inferred value stays inferred.
    assert edited["cvss"]["inferred"] is True
    assert edited["mitre"]["inferred"] is True


def test_operator_can_set_cvss_and_mitre_by_hand_and_owns_the_provenance() -> None:
    """A hand-entered value is author-stated, so ``inferred`` flips to false (FR-19)."""
    client, finding_id = _client_with_scored_finding()

    edited = client.post(
        f"/api/findings/{finding_id}",
        json={
            "title": "SQLi login",
            "severity": "high",
            "cvss": {"vector": "CVSS:3.1/AV:N/AC:H/PR:L", "base_score": 7.2},
            "mitre": {"techniques": ["T1190", "T1110"]},
            "reason": "scored by hand from the report",
        },
    ).json()

    assert edited["cvss"] == {
        "vector": "CVSS:3.1/AV:N/AC:H/PR:L",
        "base_score": 7.2,
        "inferred": False,
    }
    assert edited["mitre"] == {"techniques": ["T1190", "T1110"], "inferred": False}


def test_resubmitting_an_inferred_value_unchanged_does_not_launder_it() -> None:
    """Round-tripping the form must not turn a model guess into an author claim."""
    client, finding_id = _client_with_scored_finding()

    edited = client.post(
        f"/api/findings/{finding_id}",
        json={
            "title": "SQLi login",
            "severity": "high",
            "cvss": {"vector": "CVSS:3.1/AV:N/AC:L", "base_score": 9.8},
            "mitre": {"techniques": ["T1190"]},
        },
    ).json()

    assert edited["cvss"]["inferred"] is True
    assert edited["mitre"]["inferred"] is True


def test_operator_can_add_taxonomy_to_a_finding_that_had_none() -> None:
    """The manual-entry path never sets CVSS/ATT&CK, so editing is how they arrive."""
    client, finding_id = _client_with_finding()

    edited = client.post(
        f"/api/findings/{finding_id}",
        json={
            "title": "SQLi login",
            "severity": "high",
            "cvss": {"vector": "CVSS:3.1/AV:N/AC:L/PR:N", "base_score": 9.1},
            "mitre": {"techniques": ["T1190"]},
        },
    ).json()

    assert edited["cvss"]["base_score"] == 9.1
    assert edited["cvss"]["inferred"] is False
    assert edited["mitre"]["techniques"] == ["T1190"]


def test_edit_rejects_an_out_of_range_cvss_score() -> None:
    client, finding_id = _client_with_finding()

    resp = client.post(
        f"/api/findings/{finding_id}",
        json={"title": "x", "severity": "high", "cvss": {"vector": "v", "base_score": 11.0}},
    )

    assert resp.status_code == 422
