"""Tests for domain enums and schemas."""

from revalid.domain import RetestSessionStatus, SessionEventKind


def test_retest_session_status_terminal_set() -> None:
    assert RetestSessionStatus.STARTING.value == "starting"
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
