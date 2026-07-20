"""Unit tests for the FR-16 finding revision & annotation service (ADR-0024).

In-memory SQLite, no I/O. Covers the append-only version lifecycle (extraction =
v1, each edit a new immutable version, current = highest) and the stage-tagged,
append-only notes log.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.domain import CvssCode, Finding, FindingStage, MitreMapping, Severity
from revalid.findings import (
    add_note,
    add_version,
    create_finding,
    current_version,
    list_notes,
    list_versions,
)


def _session() -> Session:
    return session_factory(create_db_engine(IN_MEMORY))()


def _finding(title: str = "SQLi login", description: str = "") -> Finding:
    return Finding(title=title, severity=Severity.HIGH, description=description)


def test_create_finding_lands_extraction_version_1() -> None:
    session = _session()
    record = create_finding(session, _finding(description="original"), report_id=7)
    session.commit()

    assert record.report_id == 7
    current = current_version(session, record.id)
    assert current is not None
    assert current.version == 1
    assert current.origin == "extraction"
    assert current.edited_by is None
    assert current.to_domain().description == "original"


def test_edit_appends_a_new_immutable_version() -> None:
    session = _session()
    record = create_finding(session, _finding(description="v1 text"))
    session.commit()

    add_version(
        session, record.id, _finding(description="v2 text"), edited_by="user", reason="fix typo"
    )

    versions = list_versions(session, record.id)
    assert [v.version for v in versions] == [1, 2]
    # v1 is untouched — the correction is a new row, not a mutation (append-only).
    assert versions[0].origin == "extraction"
    assert versions[0].to_domain().description == "v1 text"
    assert versions[1].origin == "edit"
    assert versions[1].edited_by == "user"
    assert versions[1].reason == "fix typo"
    # The current version is the newest.
    current = current_version(session, record.id)
    assert current is not None
    assert current.version == 2
    assert current.to_domain().description == "v2 text"


def test_edits_keep_bumping_the_version() -> None:
    session = _session()
    record = create_finding(session, _finding())
    session.commit()
    for i in range(2, 5):
        add_version(session, record.id, _finding(description=f"v{i}"))
    assert [v.version for v in list_versions(session, record.id)] == [1, 2, 3, 4]


def test_current_version_is_none_for_absent_finding() -> None:
    session = _session()
    assert current_version(session, 999) is None
    assert list_versions(session, 999) == []


def test_notes_are_appended_and_listed_newest_first() -> None:
    session = _session()
    record = create_finding(session, _finding())
    session.commit()

    add_note(session, record.id, FindingStage.PLAN, "first note")
    add_note(session, record.id, FindingStage.VERDICT, "second note", author="reviewer")

    notes = list_notes(session, record.id)
    assert [n.body for n in notes] == ["second note", "first note"]
    assert notes[0].stage == "verdict"
    assert notes[0].author == "reviewer"
    assert notes[1].stage == "plan"


def test_notes_empty_for_finding_without_notes() -> None:
    session = _session()
    record = create_finding(session, _finding())
    session.commit()
    assert list_notes(session, record.id) == []


def test_cvss_and_mitre_survive_the_version_round_trip() -> None:
    # FR-19: taxonomy fields are stored as columns and rebuilt by to_domain, so
    # they survive persistence — not only inside the raw audit blob.
    session = _session()
    finding = Finding(
        title="SQLi login",
        severity=Severity.HIGH,
        cvss=CvssCode(
            vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            base_score=9.8,
            inferred=True,
        ),
        mitre=MitreMapping(techniques=("T1190",), inferred=True),
    )
    record = create_finding(session, finding)
    session.commit()

    current = current_version(session, record.id)
    assert current is not None
    reloaded = current.to_domain()
    assert reloaded.cvss.base_score == 9.8
    assert reloaded.cvss.inferred is True
    assert reloaded.mitre.techniques == ("T1190",)
    assert reloaded.mitre.inferred is True
