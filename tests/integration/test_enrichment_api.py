"""Opt-in taxonomy enrichment over the real HTTP surface (FR-19/FR-02, issue #233).

The decision this pins: the JSON and manual doors stay **LLM-free by default**.
The strongest form of that guarantee is not "it is fast" but "no model is
invoked at all", so the default-path tests inject an agent that raises if it is
ever called — if the default ever starts enriching, they fail loudly.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from revalid.app import create_app, get_taxonomy_agent
from revalid.db import IN_MEMORY, create_db_engine
from revalid.extract import build_taxonomy_agent

pytestmark = pytest.mark.integration

_DERIVED: dict[str, Any] = {
    "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "cvss_base_score": 9.8,
    "mitre_techniques": ["T1190"],
}

_FINDINGS: list[dict[str, Any]] = [
    {"title": "SQL injection in login", "severity": "high", "description": "Concatenated SQL."},
    {"title": "IDOR on basket", "severity": "medium", "description": "No ownership check."},
]


def _deriving_model() -> FunctionModel:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=_DERIVED)])

    return FunctionModel(respond)


def _forbidden_model() -> FunctionModel:
    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise AssertionError("the LLM-free door called a model without being asked to")

    return FunctionModel(respond)


def _client(model: FunctionModel) -> TestClient:
    app = create_app(engine=create_db_engine(IN_MEMORY))
    app.dependency_overrides[get_taxonomy_agent] = lambda: build_taxonomy_agent(model)
    return TestClient(app)


def test_import_without_the_flag_invokes_no_model() -> None:
    with _client(_forbidden_model()) as client:
        response = client.post("/api/findings/import", json={"findings": _FINDINGS})

        assert response.status_code == 200
        assert response.json() == {"imported": 2, "enriched": 0, "enrichment_failed": 0}
        for finding in client.get("/api/findings").json():
            assert finding["cvss"]["vector"] == ""
            assert finding["mitre"]["techniques"] == []


def test_import_with_the_flag_derives_and_flags_the_taxonomy() -> None:
    with _client(_deriving_model()) as client:
        response = client.post(
            "/api/findings/import", params={"enrich": "true"}, json={"findings": _FINDINGS}
        )

        assert response.status_code == 200
        assert response.json() == {"imported": 2, "enriched": 2, "enrichment_failed": 0}
        for finding in client.get("/api/findings").json():
            assert finding["cvss"]["vector"] == _DERIVED["cvss_vector"]
            assert finding["cvss"]["inferred"] is True
            assert finding["mitre"]["techniques"] == ["T1190"]
            assert finding["mitre"]["inferred"] is True


def test_import_keeps_a_stated_cvss_even_when_enriching() -> None:
    """The export stated it, so it is copied — the model does not get to overrule it."""
    stated = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"
    with _client(_deriving_model()) as client:
        client.post(
            "/api/findings/import",
            params={"enrich": "true"},
            json={
                "findings": [
                    {"title": "Weak TLS", "severity": "low", "cvssv3": stated, "cvssv3_score": 3.1}
                ]
            },
        )

        [finding] = client.get("/api/findings").json()
        assert finding["cvss"]["vector"] == stated
        assert finding["cvss"]["inferred"] is False
        assert finding["mitre"]["inferred"] is True  # the unstated half is derived


def test_manual_report_without_the_flag_invokes_no_model() -> None:
    with _client(_forbidden_model()) as client:
        response = client.post(
            "/api/reports/manual", json={"label": "Manual", "findings": _FINDINGS}
        )

        assert response.status_code == 201
        assert response.json()["model"] == "manual"
        for finding in client.get("/api/findings").json():
            assert finding["cvss"]["vector"] == ""


def test_manual_report_with_the_flag_enriches_and_records_the_lineage() -> None:
    with _client(_deriving_model()) as client:
        response = client.post(
            "/api/reports/manual",
            json={"label": "Manual", "findings": _FINDINGS, "enrich": True},
        )

        assert response.status_code == 201
        assert response.json()["model"] == "manual+enriched"
        for finding in client.get("/api/findings").json():
            assert finding["cvss"]["inferred"] is True
            assert finding["mitre"]["techniques"] == ["T1190"]


def test_a_failed_derivation_still_imports_and_is_reported() -> None:
    """The import succeeds; the missing taxonomy is surfaced, not swallowed."""

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"cvss_base_score": "not a number"})]
        )

    with _client(FunctionModel(respond)) as client:
        response = client.post(
            "/api/findings/import", params={"enrich": "true"}, json={"findings": _FINDINGS}
        )

        assert response.status_code == 200
        assert response.json() == {"imported": 2, "enriched": 0, "enrichment_failed": 2}
        assert len(client.get("/api/findings").json()) == 2


def test_manual_report_accepts_a_hand_typed_taxonomy_as_author_stated() -> None:
    """Typed by a person, so `inferred=false` — and no model is needed for it (#237)."""
    with _client(_forbidden_model()) as client:
        response = client.post(
            "/api/reports/manual",
            json={
                "label": "Transcribed",
                "findings": [
                    {
                        "title": "SQLi in login",
                        "severity": "high",
                        "cvssv3": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        "cvssv3_score": 9.8,
                        "mitre_techniques": ["T1190"],
                    }
                ],
            },
        )

        assert response.status_code == 201
        [finding] = client.get("/api/findings").json()
        assert finding["cvss"]["vector"] == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        assert finding["cvss"]["base_score"] == 9.8
        assert finding["cvss"]["inferred"] is False
        assert finding["mitre"]["techniques"] == ["T1190"]
        assert finding["mitre"]["inferred"] is False


def test_enrichment_never_overwrites_what_the_operator_typed() -> None:
    """The two features must compose: a typed value outranks a derived one (#237)."""
    typed = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"
    with _client(_deriving_model()) as client:
        response = client.post(
            "/api/reports/manual",
            json={
                "label": "Typed then enriched",
                "enrich": True,
                "findings": [
                    {
                        "title": "Weak TLS",
                        "severity": "low",
                        "cvssv3": typed,
                        "mitre_techniques": ["T1040"],
                    }
                ],
            },
        )

        assert response.status_code == 201
        [finding] = client.get("/api/findings").json()
        assert finding["cvss"]["vector"] == typed
        assert finding["cvss"]["inferred"] is False
        assert finding["mitre"]["techniques"] == ["T1040"]
        assert finding["mitre"]["inferred"] is False
