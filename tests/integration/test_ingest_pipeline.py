"""Integration: full FR-02 pipeline — sample file → mapping → SQLite → API."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import FindingRecord, create_db_engine, session_factory
from revalid.ingest import load_defectdojo_export

SAMPLE = Path(__file__).parent.parent / "data" / "defectdojo_sample.json"


@pytest.mark.integration
def test_sample_export_persists_and_serves(tmp_path: Path) -> None:
    findings = load_defectdojo_export(SAMPLE.read_text())
    assert len(findings) == 3

    db_path = tmp_path / "revalid.db"
    engine = create_db_engine(str(db_path))
    with session_factory(engine)() as session:
        session.add_all(FindingRecord.from_domain(f) for f in findings)
        session.commit()

    # A fresh app instance over the same file sees the data (durability).
    with TestClient(create_app(db_path=str(db_path))) as client:
        listed = client.get("/findings").json()
    assert [f["severity"] for f in listed] == ["critical", "high", "low"]
    # Unknown source fields survive the full trip (FR-02 audit criterion).
    assert listed[0]["raw"]["cwe"] == 89
    assert listed[2]["raw"]["mitigation"].startswith("Return a generic")


@pytest.mark.integration
def test_orm_roundtrip_preserves_domain_model(tmp_path: Path) -> None:
    [first, *_] = load_defectdojo_export(SAMPLE.read_text())
    engine = create_db_engine(str(tmp_path / "roundtrip.db"))
    factory = session_factory(engine)
    with factory() as session:
        session.add(FindingRecord.from_domain(first))
        session.commit()
    with factory() as session:
        stored = session.get(FindingRecord, 1)
        assert stored is not None
        assert stored.to_domain() == first
