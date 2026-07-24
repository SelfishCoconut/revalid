"""Tests for domain enums and schemas."""

from revalid.domain import AgenticEvidence, RetestSessionStatus, SessionEventKind


def test_agentic_evidence_defaults_to_explanation_only() -> None:
    ev = AgenticEvidence(explanation="login bypass still returns a token")
    assert ev.explanation == "login bypass still returns a token"
    assert ev.command == ""
    assert ev.output == ""
    assert ev.exit_code is None
    assert ev.elapsed_ms == 0.0


def test_agentic_evidence_carries_command_proof() -> None:
    ev = AgenticEvidence(
        explanation="200 + JWT",
        command="curl -s http://lab/rest/user/login",
        output='{"authentication":{"token":"eyJ..."}}',
        exit_code=0,
        elapsed_ms=42.0,
    )
    assert ev.command.startswith("curl")
    assert ev.exit_code == 0
    assert ev.model_config["frozen"] is True


def test_retest_session_status_terminal_set() -> None:
    assert RetestSessionStatus.WORKING.value == "working"
    terminal = {
        RetestSessionStatus.CONCLUDED,
        RetestSessionStatus.GIVEN_UP,
        RetestSessionStatus.ENDED,
        RetestSessionStatus.ERROR,
    }
    assert RetestSessionStatus.AWAITING_COMMAND not in terminal


def test_session_event_kind_values() -> None:
    assert SessionEventKind.COMMAND_PROPOSED.value == "command_proposed"
    assert {k.value for k in SessionEventKind} >= {
        "command_proposed",
        "command_output",
        "verdict",
        "state_change",
    }
