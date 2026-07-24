"""Unit tests for the FR-17 retest-session persistence layer (ADR-0025, Slice 0).

In-memory SQLite, no I/O. Covers the append-only transcript (monotonic ``seq``),
status transitions, and verdict recording on :class:`~revalid.db.RetestSessionRecord`,
plus the Task 5 orchestration layer that drives the Task 4 agent step-by-step.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests._retest_helpers import (
    has_command_result,
    script_always_propose,
    script_await_operator,
    script_await_then_conclude_on_message,
    script_conclude_inconclusive,
    script_inconclusive_then_conclude_on_message,
    script_respond_then_conclude,
    script_run_then_conclude,
    script_run_then_conclude_noting_message,
    streaming,
)

from revalid import retest_session as rs
from revalid.db import IN_MEMORY, VerdictRecord, create_db_engine, session_factory
from revalid.deltas import DeltaChannel
from revalid.domain import (
    Finding,
    RetestSessionStatus,
    SessionEventKind,
    Severity,
    VerdictStatus,
)
from revalid.findings import create_finding
from revalid.retest_agent import build_retest_agent
from revalid.retest_session import SessionRegistry, apply_decision, end_session, start_and_step
from revalid.sandbox import CommandResult, FakeSandbox


def _propose_then_boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Propose ``run_command`` once, then raise on the next call (agent-crash simulation)."""
    if len(messages) <= 1:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="run_command", args={"command": "id", "rationale": "x"})]
        )
    raise RuntimeError("model backend unavailable")


def _always_boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Always raise (agent-crash simulation for the first-step error boundary)."""
    raise RuntimeError("model backend unavailable")


def _seed_finding(session: Session) -> int:
    record = create_finding(
        session, Finding(title="SQLi", severity=Severity.HIGH, description="login bypass")
    )
    session.commit()  # create_finding only flushes; the caller commits
    return record.id


def _pending_cid(registry: SessionRegistry, session_id: int) -> str:
    """Return the session's currently pending ``tool_call_id`` (must be live+pending)."""
    live = registry.get(session_id)
    assert live is not None
    assert live.pending_call_id is not None
    return live.pending_call_id


def test_create_session_starts_in_working_status() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        assert s.id is not None
        assert s.finding_id == fid
        assert s.status == RetestSessionStatus.WORKING.value
        assert s.model == "ollama:qwen3.6:27b"
        assert s.verdict_status is None
        assert s.ended_at is None
        # Default config: gated (per-command approval), no budget (ADR-0034).
        assert s.free_launch is False


def test_create_session_persists_free_launch() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m", free_launch=True)
        assert s.free_launch is True


def test_append_event_assigns_monotonic_seq() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "working"})
        rs.append_event(session, s.id, SessionEventKind.COMMAND_PROPOSED, {"command": "id"})
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["kind"] == "command_proposed"
    assert events[1]["payload"]["command"] == "id"


def test_load_events_after_filters_by_seq() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "working"})
        rs.append_event(session, s.id, SessionEventKind.COMMAND_PROPOSED, {"command": "id"})
        events = rs.load_events_after(session, s.id, after_seq=1)
    assert [e["seq"] for e in events] == [2]


def test_set_status_updates_row_and_appends_state_change_event() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.set_status(session, s.id, RetestSessionStatus.AWAITING_COMMAND)
        session.refresh(s)
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
    assert len(events) == 1
    assert events[0]["kind"] == "state_change"
    assert events[0]["payload"] == {"to": "awaiting_command"}


def test_set_status_on_unknown_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        rs.set_status(session, 999, RetestSessionStatus.ENDED)  # must not raise


def test_record_verdict_writes_row_fields() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.record_verdict(session, s.id, VerdictStatus.STILL_OPEN, "auth still bypassable")
        session.refresh(s)
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_status == "still_open"
    assert s.verdict_rationale == "auth still bypassable"
    assert s.ended_at is not None
    assert events[-2]["kind"] == "verdict"
    assert events[-2]["payload"] == {
        "status": "still_open",
        "rationale": "auth still bypassable",
    }


def test_record_verdict_appends_concluded_state_change_event() -> None:
    """The frontend derives session status only from the latest state_change event

    (Fix 2, final-review): without this, the UI would never see a transition out
    of the pre-verdict status after a normal conclude.
    """
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.record_verdict(session, s.id, VerdictStatus.FIXED, "patched")
        events = rs.load_events_after(session, s.id, after_seq=0)
    assert events[-1]["kind"] == "state_change"
    assert events[-1]["payload"] == {"to": "concluded"}


def test_record_verdict_on_unknown_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        rs.record_verdict(session, 999, VerdictStatus.FIXED, "n/a")  # must not raise


def test_record_verdict_captures_last_command_as_evidence() -> None:
    """The agentic verdict's evidence is the real last command output (Slice 6b-i)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        sid = rs.create_session(session, finding_id=fid, model="m").id
        rs.append_event(
            session,
            sid,
            SessionEventKind.COMMAND_OUTPUT,
            {
                "command": "curl -s http://lab/login",
                "stdout": "{token}",
                "stderr": "",
                "exit_code": 0,
                "elapsed_ms": 12,
            },
        )
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
        [row] = session.scalars(select(VerdictRecord)).all()
        assert row.evidence is not None
        assert row.evidence["explanation"] == "auth still bypassable"
        assert row.evidence["command"] == "curl -s http://lab/login"
        assert row.evidence["output"].startswith("{token}")
        assert row.evidence["exit_code"] == 0
        assert row.evidence["elapsed_ms"] == 12


def test_record_verdict_evidence_is_explanation_only_without_a_command() -> None:
    """A verdict reached with no command run is explanation-only, still valid (Slice 6b-i)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        sid = rs.create_session(session, finding_id=fid, model="m").id
        rs.record_verdict(session, sid, VerdictStatus.INCONCLUSIVE, "cannot tell")
        [row] = session.scalars(select(VerdictRecord)).all()
        assert row.evidence is not None
        assert row.evidence["explanation"] == "cannot tell"
        assert row.evidence["command"] == ""
        assert row.evidence["output"] == ""
        assert row.evidence["exit_code"] is None


def test_record_verdict_auto_persists_agentic_verdict() -> None:
    """Concluding a session writes a queryable agentic VerdictRecord (FR-09 wiring, Slice 6a)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        sid = s.id
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "auth still bypassable")
        [row] = session.scalars(select(VerdictRecord)).all()
        assert row.actor == "agent"
        assert row.finding_id == fid
        assert row.session_id == sid
        assert row.status == "still_open"
        assert row.rationale == "auth still bypassable"
        # Slice 6b-i: with no command run, evidence is explanation-only (not null).
        assert row.evidence == {
            "explanation": "auth still bypassable",
            "command": "",
            "output": "",
            "exit_code": None,
            "elapsed_ms": 0.0,
        }


def test_adjudicate_appends_event_and_superseding_operator_record() -> None:
    """Adjudicating a concluded session appends an event + a superseding operator row (Slice 6a)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        sid = s.id
        rs.record_verdict(session, sid, VerdictStatus.STILL_OPEN, "agent says still open")
        rs.adjudicate_verdict(session, sid, VerdictStatus.FIXED, "human confirms patched")

        rows = session.scalars(select(VerdictRecord).order_by(VerdictRecord.id)).all()
        agent_row, operator_row = rows
        assert agent_row.actor == "agent"
        assert agent_row.status == "still_open"  # the agent's record is never mutated
        assert operator_row.actor == "operator"
        assert operator_row.reason_code == "operator_adjudication"
        assert operator_row.status == "fixed"
        assert operator_row.id > agent_row.id  # supersedes: latest-per-finding wins
        assert operator_row.session_id == sid

        events = rs.load_events_after(session, sid, after_seq=0)
        adjudications = [
            e for e in events if e["kind"] == SessionEventKind.VERDICT_ADJUDICATED.value
        ]
        assert adjudications == [
            {
                "seq": adjudications[0]["seq"],
                "kind": "verdict_adjudicated",
                "payload": {"status": "fixed", "rationale": "human confirms patched"},
            }
        ]
        session.refresh(s)
        assert s.verdict_status == "fixed"  # session row reflects the final call
        assert s.verdict_rationale == "human confirms patched"


def test_adjudicate_is_noop_without_a_verdict() -> None:
    """A session with no agent verdict yet cannot be adjudicated (Slice 6a)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.adjudicate_verdict(session, s.id, VerdictStatus.FIXED, "premature")
        assert session.scalars(select(VerdictRecord)).all() == []


def test_adjudicate_unknown_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        rs.adjudicate_verdict(session, 999, VerdictStatus.FIXED, "n/a")  # must not raise


def _latest_guidance_reason(session: Session, session_id: int) -> str:
    """The text of the most recent agent hand-back message (ADR-0034/0039/0042).

    Guided one-action reports, verdict recommendations, and "I'm stuck" hand-backs are
    surfaced as ordinary ``agent_message`` events now (``needs_guidance`` folded into
    ``awaiting_operator``), so the reason lives in the message text.
    """
    messages = [
        e
        for e in rs.load_events_after(session, session_id, 0)
        if e["kind"] == SessionEventKind.AGENT_MESSAGE.value
    ]
    assert messages, "expected an agent hand-back message"
    return str(messages[-1]["payload"]["text"])


def test_guided_approve_runs_command_then_hands_back() -> None:
    """Guided mode (ADR-0039): approving runs the command, then hands back — it never
    chains to a verdict. The agent's determination is a recommendation, not a ruling."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
        agent = build_retest_agent(streaming(script_run_then_conclude))

        start_and_step(session, registry, s.id, agent, box, "Retest the SQLi finding.")
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
        assert "command_proposed" in kinds

        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
        # The command ran, but the agent's `still_open` is surfaced as a recommendation
        # at a guidance pause — no terminal verdict, sandbox kept alive for the operator.
        assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
        assert s.verdict_status is None
        assert box.commands and not box.stopped  # ran once, NOT torn down
        assert "auth still bypassable" in _latest_guidance_reason(session, s.id)

        events_before = rs.load_events_after(session, s.id, 0)
        # A repeat decision with the now-consumed cid must no-op: the pending call was
        # cleared on approval, so no command runs twice and no event is appended.
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
        events_after = rs.load_events_after(session, s.id, 0)
    assert len(box.commands) == 1  # still exactly one execution, not two
    assert events_after == events_before
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value


def test_apply_decision_wrong_command_id_is_a_noop() -> None:
    """A decision whose ``command_id`` doesn't match the pending call is a no-op.

    Guards against a stale or mismatched ``cid`` acting on the wrong command
    (final-review Fix 1): the sandbox must never run anything and the session
    must stay exactly where it was (``awaiting_command``).
    """
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([])  # nothing scripted: must never be called
        agent = build_retest_agent(streaming(script_run_then_conclude))

        start_and_step(session, registry, s.id, agent, box, "Retest.")
        apply_decision(session, registry, s.id, approved=True, command_id="not-the-pending-id")
        session.refresh(s)
    assert box.commands == []
    assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
    live = registry.get(s.id)
    assert live is not None and live.pending_call_id is not None  # still pending, untouched


def test_apply_decision_reject_never_executes_but_still_hands_back() -> None:
    """Denying the proposed command never touches the sandbox; the resumed agent then
    hands back (guided, ADR-0039) instead of the command silently running."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([])  # nothing scripted: must never be called
        agent = build_retest_agent(streaming(script_run_then_conclude))

        start_and_step(session, registry, s.id, agent, box, "Retest.")
        cid = _pending_cid(registry, s.id)
        apply_decision(
            session, registry, s.id, approved=False, reason="out of scope host", command_id=cid
        )
        session.refresh(s)
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
    assert "command_rejected" in kinds
    assert box.commands == []
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value


def test_free_launch_auto_runs_command_to_verdict() -> None:
    """In free-launch, start_and_step drives the command to a verdict — no human decision."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m", free_launch=True)
        box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
        agent = build_retest_agent(streaming(script_run_then_conclude))

        # start_and_step's own _advance loop runs before this returns.
        start_and_step(session, registry, s.id, agent, box, "Retest.", free_launch=True)
        session.refresh(s)
        events = rs.load_events_after(session, s.id, 0)
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_status == "still_open"
    assert len(box.commands) == 1  # the command ran, unattended
    approvals = [e for e in events if e["kind"] == SessionEventKind.COMMAND_APPROVED.value]
    assert len(approvals) == 1
    assert all(e["payload"].get("auto") is True for e in approvals)
    # No human approval/rejection events were needed to reach the verdict.
    assert not any(e["kind"] == SessionEventKind.COMMAND_REJECTED.value for e in events)


def test_guided_parks_after_one_command_discarding_the_next_proposal() -> None:
    """Guided mode does exactly one action then parks (ADR-0039): even an agent that
    keeps proposing is stopped after a single approved command, its next proposal
    surfaced as an advisory suggestion rather than opening another gate."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox(
            lambda cmd: CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)
        )
        agent = build_retest_agent(streaming(script_always_propose))  # never concludes
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        apply_decision(
            session, registry, s.id, approved=True, command_id=_pending_cid(registry, s.id)
        )
        session.refresh(s)
        live = registry.get(s.id)
        reason = _latest_guidance_reason(session, s.id)
        events = rs.load_events_after(session, s.id, 0)
        proposed = [e for e in events if e["kind"] == "command_proposed"]
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value  # parked, not re-gated
    assert len(box.commands) == 1  # exactly one command ran
    assert live is not None and live.pending_call_id is None  # the next proposal was discarded
    assert not box.stopped  # sandbox kept alive so the operator can keep steering
    assert "keep probing" in reason  # the discarded proposal surfaced as a suggestion
    assert len(proposed) == 1  # only the first (approved) command was ever gated


def test_continue_after_a_guided_discard_park_resumes_cleanly() -> None:
    """After a guided one-action park that discarded a proposal, continuing re-runs the
    agent from the trimmed history without error and gates its next command (ADR-0039)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox(
            lambda cmd: CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)
        )
        agent = build_retest_agent(streaming(script_always_propose))
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        apply_decision(
            session, registry, s.id, approved=True, command_id=_pending_cid(registry, s.id)
        )
        session.refresh(s)
        assert (
            s.status == RetestSessionStatus.AWAITING_OPERATOR.value
        )  # parked (proposal discarded)
        rs.submit_message(session, registry, s.id, "keep going then")
        rs.continue_session(session, registry, s.id)  # re-run from the trimmed history
        session.refresh(s)
        live = registry.get(s.id)
    assert s.status == RetestSessionStatus.AWAITING_COMMAND.value  # proposes again, gated
    assert live is not None and live.pending_call_id is not None
    assert len(box.commands) == 1  # the re-proposal awaits approval; nothing ran yet


def test_agent_inconclusive_conclusion_pauses_for_guidance() -> None:
    """The agent handing back `inconclusive` pauses and asks — no verdict is written (ADR-0034)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_conclude_inconclusive))
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        session.refresh(s)
        rows = session.scalars(select(VerdictRecord)).all()
        events = rs.load_events_after(session, s.id, 0)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert s.verdict_status is None
    assert rows == []  # the agent cannot self-conclude inconclusive
    assert registry.get(s.id) is not None and not box.stopped  # asks with the sandbox alive
    messages = [e for e in events if e["kind"] == SessionEventKind.AGENT_MESSAGE.value]
    assert messages[-1]["payload"]["text"] == "exhausted my options, need guidance"


def test_continue_after_guidance_resumes_with_the_operators_steer() -> None:
    """Keep going after an exhausted-options pause re-runs the agent with the operator's
    steer (ADR-0034); in guided mode the steered agent *recommends* a determination for
    the operator to confirm rather than recording it itself (ADR-0039)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_inconclusive_then_conclude_on_message))
        start_and_step(session, registry, s.id, agent, box, "Retest.")  # pauses (inconclusive)
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
        rs.submit_message(session, registry, s.id, "try the admin endpoint")  # operator steer
        rs.continue_session(session, registry, s.id)  # re-runs the agent with the message
        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value  # a recommendation, not a ruling
    assert s.verdict_status is None
    # The steer reached the agent: its second turn concluded citing the operator's steer.
    assert "with the operator's steer, confirmed open" in reason


def test_conclude_session_records_operator_verdict_and_tears_down() -> None:
    """The operator manually concluding a paused session writes their verdict + tears down."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_conclude_inconclusive))
        start_and_step(session, registry, s.id, agent, box, "Retest.")  # pauses (needs_guidance)
        rs.conclude_session(
            session, registry, s.id, VerdictStatus.INCONCLUSIVE, "I checked; can't tell"
        )
        session.refresh(s)
        rows = session.scalars(select(VerdictRecord)).all()
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_status == "inconclusive"
    assert len(rows) == 1
    assert rows[0].actor == "operator"
    assert rows[0].reason_code == "operator_conclusion"
    assert registry.get(s.id) is None and box.stopped  # torn down


def test_conclude_session_from_awaiting_command_writes_verdict() -> None:
    """Conclude-anytime (#150): the operator concludes while a command awaits approval."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, box, "Retest.")  # → awaiting_command
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_COMMAND.value  # a command is pending
        rs.conclude_session(session, registry, s.id, VerdictStatus.STILL_OPEN, "seen enough")
        session.refresh(s)
        rows = session.scalars(select(VerdictRecord)).all()
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_status == "still_open"
    assert rows and rows[0].actor == "operator"
    assert registry.get(s.id) is None and box.stopped  # torn down


def test_fail_does_not_clobber_a_concluded_session() -> None:
    """A late agent-step failure after an operator conclude must not overwrite the verdict (#150).

    The conclude-anytime race guard in :func:`_fail`.
    """
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        rs.conclude_session(session, registry, s.id, VerdictStatus.FIXED, "operator call")
        # An in-flight step that raised *after* the conclude routes here; it must no-op.
        rs._fail(session, registry, s.id, "sandbox vanished mid-exec")
        session.refresh(s)
        events = rs.load_events_after(session, s.id, 0)
    assert s.status == RetestSessionStatus.CONCLUDED.value  # not clobbered to error
    assert s.verdict_status == "fixed"
    assert not any(e["kind"] == SessionEventKind.ERROR.value for e in events)


def test_stop_pauses_keeping_sandbox_then_resume_reopens_pending_command() -> None:
    """Stop parks a live session in `stopped` (sandbox alive); Resume re-opens the gate (#150)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        cid = _pending_cid(registry, s.id)

        rs.stop_session(session, registry, s.id)
        session.refresh(s)
        assert s.status == RetestSessionStatus.STOPPED.value
        live = registry.get(s.id)
        assert live is not None and live.stopped and not box.stopped  # sandbox kept alive
        assert live.pending_call_id == cid  # the proposed command is held, not dropped

        rs.resume_session(session, registry, s.id)
        session.refresh(s)
    assert s.status == RetestSessionStatus.AWAITING_COMMAND.value  # gate re-opened
    assert registry.get(s.id) is not None  # still live


def test_stop_halts_the_free_launch_auto_run_loop() -> None:
    """A stopped session's free-launch loop runs nothing (#150)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([CommandResult(stdout="x", stderr="", exit_code=0, elapsed_ms=1)])
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, box, "Retest.")  # gated → pending command
        live = registry.get(s.id)
        assert live is not None
        live.free_launch = True
        live.stopped = True  # operator stopped
        rs._advance(session, registry, s.id)  # must respect the stop
    assert box.commands == []  # nothing auto-ran while stopped


def test_create_session_deferred_opens_idle_and_reads_back_goal_and_scope() -> None:
    """A deferred (Restart) session opens `idle`; its recorded goal + scope read back (#150)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m", deferred=True)
        assert s.status == RetestSessionStatus.IDLE.value
        rs.append_event(session, s.id, SessionEventKind.TARGET_SET, {"endpoints": ["http://t/x"]})
        rs.append_event(
            session, s.id, SessionEventKind.PLAN_UPDATED, {"steps": ["step a", "step b"]}
        )
        assert rs.session_scope(session, s.id) == ("http://t/x",)
        assert rs.session_goal(session, s.id) == ("step a", "step b")


def test_enable_free_launch_auto_approves_pending_command() -> None:
    """Turning free-launch on with a command pending drives it to a verdict."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
        agent = build_retest_agent(streaming(script_run_then_conclude))

        # Start gated: the first command proposal waits for a decision.
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        assert _pending_cid(registry, s.id)  # a command is pending
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_COMMAND.value

        rs.set_free_launch(session, registry, s.id, True)
        session.refresh(s)
        events = rs.load_events_after(session, s.id, 0)
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.free_launch is True
    assert len(box.commands) == 1  # auto-approved and run without a human decision
    assert SessionEventKind.FREE_LAUNCH_CHANGED.value in [e["kind"] for e in events]
    approvals = [e for e in events if e["kind"] == SessionEventKind.COMMAND_APPROVED.value]
    assert approvals and all(e["payload"].get("auto") is True for e in approvals)


def test_disable_free_launch_records_event_on_live_session() -> None:
    """Turning free-launch off on a live (parked-at-command) session records the toggle."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_run_then_conclude))

        # Gated start parks at the proposed command (still live).
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        assert registry.get(s.id) is not None  # still live at the command gate

        rs.set_free_launch(session, registry, s.id, False)
        live = registry.get(s.id)
        assert live is not None
        assert live.free_launch is False
        session.refresh(s)
        events = rs.load_events_after(session, s.id, 0)
    assert s.free_launch is False
    toggles = [e for e in events if e["kind"] == SessionEventKind.FREE_LAUNCH_CHANGED.value]
    assert toggles[-1]["payload"] == {"enabled": False}


def test_set_free_launch_noop_when_not_live() -> None:
    """Toggling a session that is not live is a no-op (no raise, record untouched)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()  # nothing registered
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.set_free_launch(session, registry, s.id, True)  # no live session → no-op
        session.refresh(s)
        assert s.free_launch is False


def test_is_terminal_matches_the_terminal_statuses() -> None:
    assert rs.is_terminal(RetestSessionStatus.CONCLUDED)
    assert rs.is_terminal(RetestSessionStatus.GIVEN_UP)
    assert rs.is_terminal(RetestSessionStatus.ENDED)
    assert rs.is_terminal(RetestSessionStatus.ERROR)
    assert not rs.is_terminal(RetestSessionStatus.IDLE)
    assert not rs.is_terminal(RetestSessionStatus.WORKING)
    assert not rs.is_terminal(RetestSessionStatus.AWAITING_COMMAND)
    assert not rs.is_terminal(RetestSessionStatus.AWAITING_OPERATOR)
    assert not rs.is_terminal(RetestSessionStatus.STOPPED)


def test_apply_decision_on_unknown_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        apply_decision(session, registry, 999, approved=True, command_id="x")  # must not raise


def test_end_session_tears_down_and_marks_ended() -> None:
    """An operator can end a live session mid-flight: sandbox stops, status -> ended."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([])
        agent = build_retest_agent(streaming(script_always_propose))
        start_and_step(session, registry, s.id, agent, box, "Retest.")

        end_session(session, registry, s.id)
        session.refresh(s)
    assert s.status == RetestSessionStatus.ENDED.value
    assert box.stopped
    assert registry.get(s.id) is None


def test_end_session_on_already_terminal_session_is_a_noop() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.record_verdict(session, s.id, VerdictStatus.FIXED, "patched")

        end_session(session, registry, s.id)
        session.refresh(s)
    assert s.status == RetestSessionStatus.CONCLUDED.value  # untouched, not overwritten to "ended"


def test_start_and_step_agent_error_sets_error_status_and_tears_down() -> None:
    """A crashing agent on the first step trips the orchestration boundary, not the caller."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([])
        agent = build_retest_agent(streaming(_always_boom))

        start_and_step(session, registry, s.id, agent, box, "Retest.")
        session.refresh(s)
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
    assert s.status == RetestSessionStatus.ERROR.value
    assert "error" in kinds
    assert box.stopped
    assert registry.get(s.id) is None


def test_apply_decision_agent_error_on_resume_sets_error_status_and_tears_down() -> None:
    """A crash on the resumed run (after approval) is caught at the same boundary."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)])
        agent = build_retest_agent(streaming(_propose_then_boom))
        start_and_step(session, registry, s.id, agent, box, "Retest.")

        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
    assert s.status == RetestSessionStatus.ERROR.value
    assert "error" in kinds
    assert box.stopped
    assert registry.get(s.id) is None


def _echo_box() -> FakeSandbox:
    """A FakeSandbox that echoes each command's text, for deterministic multi-command tests."""
    return FakeSandbox(
        lambda cmd: CommandResult(stdout=f"out:{cmd}", stderr="", exit_code=0, elapsed_ms=1)
    )


def _saw_operator_activity(messages: list[ModelMessage]) -> bool:
    """True once a run_command tool return carrying operator activity is in history."""
    return any(
        isinstance(part, ToolReturnPart) and "operator activity" in str(part.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
    )


def _conclude_noting_operator(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Propose once, then conclude with a rationale reporting whether it saw the human's command."""
    if not has_command_result(messages):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_command",
                    args={
                        "command": "curl -s http://revalid-juice-shop:3000/",
                        "rationale": "probe",
                    },
                )
            ]
        )
    rationale = "saw-operator" if _saw_operator_activity(messages) else "no-operator"
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={"status": "still_open", "rationale": rationale},
            )
        ]
    )


def test_submit_human_command_records_event_and_buffers_observation() -> None:
    """A manual `!` command runs ungated, is recorded, and is buffered for the agent."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_human_command(session, registry, s.id, "whoami")

        human = [e for e in rs.load_events_after(session, s.id, 0) if e["kind"] == "human_command"]
        assert len(human) == 1
        assert human[0]["payload"]["command"] == "whoami"
        assert human[0]["payload"]["stdout"] == "out:whoami"
        live = registry.get(s.id)
        assert live is not None
        assert len(live.observations) == 1
        assert "whoami" in live.observations[0]


def test_agent_observes_human_command_on_next_turn() -> None:
    """The human's manual command is surfaced to the agent when the run resumes."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(_conclude_noting_operator))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_human_command(session, registry, s.id, "whoami")  # while awaiting approval
        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)

        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert "saw-operator" in reason  # the agent read the operator activity, then handed back


def test_reject_folds_operator_activity_into_the_denial() -> None:
    """A rejection surfaces buffered operator activity to the agent via the denial message."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(_conclude_noting_operator))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_human_command(session, registry, s.id, "whoami")
        cid = _pending_cid(registry, s.id)
        # Reject the agent's own proposal: no command runs, but the denial the
        # agent reads still carries what the operator did.
        apply_decision(session, registry, s.id, approved=False, reason="not that", command_id=cid)

        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert "saw-operator" in reason


def test_submit_human_command_on_dead_session_is_a_noop() -> None:
    """Submitting a command to a non-live session records nothing and does not raise."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        rs.submit_human_command(session, registry, 999, "whoami")  # never started
        assert rs.load_events_after(session, 999, 0) == []
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_message(session, registry, s.id, "focus on the login endpoint")

        msgs = [e for e in rs.load_events_after(session, s.id, 0) if e["kind"] == "human_message"]
        assert len(msgs) == 1
        assert msgs[0]["payload"]["text"] == "focus on the login endpoint"
        live = registry.get(s.id)
        assert live is not None
        assert live.human_messages == ["focus on the login endpoint"]


def test_submit_message_records_even_when_not_live() -> None:
    """A message to an existing but non-live session is still recorded, never lost.

    A session that outlived a backend restart has no live agent, so the message cannot
    be buffered for delivery — but it must still land on the transcript rather than be
    silently dropped (ADR-0042).
    """
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")  # no LiveSession registered
        rs.submit_message(session, registry, s.id, "hello")
        events = rs.load_events_after(session, s.id, 0)
    assert [e["kind"] for e in events] == ["human_message"]
    assert events[-1]["payload"]["text"] == "hello"


def test_agent_reads_queued_message_on_approve() -> None:
    """A queued chat message reaches the agent as a user turn on the next approval."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_message(session, registry, s.id, "focus on the login endpoint")
        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)

        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert "saw-message" in reason


def test_agent_reads_queued_message_on_reject() -> None:
    """A queued chat message is delivered even when the pending command is rejected."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_message(session, registry, s.id, "focus on the login endpoint")
        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=False, reason="no", command_id=cid)

        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert "saw-message" in reason


def test_no_message_means_no_extra_user_turn() -> None:
    """Without a chat message the agent sees only the initial goal (control)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)

        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert "no-message" in reason


def test_respond_emits_agent_message_through_the_orchestrator() -> None:
    """The agent's `respond` prose is recorded as an `agent_message` transcript event.

    Exercises the real `_make_deps` emit_message wire end-to-end (not a stub): a
    scripted model calls `respond` then concludes, and the orchestrator must
    persist an `agent_message` event carrying the prose text.
    """
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(streaming(script_respond_then_conclude))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        session.refresh(s)
        events = rs.load_events_after(session, s.id, 0)
    prose = [e["payload"]["text"] for e in events if e["kind"] == "agent_message"]
    # The `respond` prose is recorded (guided mode then also surfaces the conclusion
    # as a recommendation agent_message, ADR-0042 — so it is among the messages).
    assert "the 500 was the WAF rejecting the payload" in prose
    # Guided (ADR-0039): the agent's `still_open` is a recommendation, so it hands back.
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value


def test_set_goal_records_plan_updated_and_queues_for_agent() -> None:
    """A live goal edit appends a plan_updated event and queues the goal for the agent (6b-ii)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox(
            lambda cmd: CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)
        )
        agent = build_retest_agent(streaming(script_always_propose))
        start_and_step(session, registry, s.id, agent, box, "Retest.")

        rs.set_goal(session, registry, s.id, ["Check the login endpoint", "Confirm the token"])
        live = registry.get(s.id)
        assert live is not None
        assert live.pending_goal == ["Check the login endpoint", "Confirm the token"]
        events = rs.load_events_after(session, s.id, 0)
    updates = [e for e in events if e["kind"] == SessionEventKind.PLAN_UPDATED.value]
    assert updates[-1]["payload"] == {"steps": ["Check the login endpoint", "Confirm the token"]}


def test_set_goal_updates_panel_even_when_not_live() -> None:
    """A non-terminal session with no live agent (e.g. after a backend restart) still
    records the plan_updated event so the panel reflects the edit — it just has no
    live agent to queue for."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.set_goal(session, registry, s.id, ["x"])  # not live -> no queue, but panel updates
        assert registry.get(s.id) is None
        events = rs.load_events_after(session, s.id, 0)
    updates = [e for e in events if e["kind"] == SessionEventKind.PLAN_UPDATED.value]
    assert updates[-1]["payload"] == {"steps": ["x"]}


def test_set_goal_is_noop_when_terminal() -> None:
    """A terminal session's goal edit is a no-op — it can no longer be steered."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.set_status(session, s.id, RetestSessionStatus.ENDED)
        before = rs.load_events_after(session, s.id, 0)
        rs.set_goal(session, registry, s.id, ["x"])  # terminal -> no raise, no event
        assert rs.load_events_after(session, s.id, 0) == before


def test_queued_goal_is_injected_into_the_next_turn() -> None:
    """A queued goal reaches the agent as a user turn on the next approval (6b-ii)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_run_then_conclude_noting_message))
        start_and_step(session, registry, s.id, agent, box, "Retest.")
        cid = _pending_cid(registry, s.id)
        rs.set_goal(session, registry, s.id, ["focus on the admin endpoint"])
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
        reason = _latest_guidance_reason(session, s.id)
    # The goal injection is delivered as a user turn -> the model reports "saw-message",
    # surfaced in the guided hand-back reason (ADR-0039).
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert "saw-message" in reason


# --- issue #204: conversational hand-back, delivered marker, turn cancel/retry ---


def test_await_operator_parks_and_replies() -> None:
    """A conversational reply parks in awaiting_operator, sandbox alive, no guidance banner."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_await_operator))
        start_and_step(session, registry, s.id, agent, box, "hi")
        session.refresh(s)
        events = rs.load_events_after(session, s.id, 0)
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert s.verdict_status is None
    assert registry.get(s.id) is not None and not box.stopped  # replies with the sandbox alive
    messages = [e for e in events if e["kind"] == SessionEventKind.AGENT_MESSAGE.value]
    assert messages[-1]["payload"]["text"] == "Hi — ready when you are."


def test_message_resumes_an_awaiting_operator_session() -> None:
    """The operator's next message resumes a session parked in awaiting_operator (#204)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_await_then_conclude_on_message))
        start_and_step(session, registry, s.id, agent, box, "hi")  # parks awaiting_operator
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
        rs.submit_message(session, registry, s.id, "yes, keep going")
        rs.continue_session(session, registry, s.id)
        session.refresh(s)
    # Guided mode (default): the resumed agent's conclusion is a recommendation,
    # so it parks in awaiting_operator rather than self-recording a verdict (ADR-0040/0042).
    assert s.status == RetestSessionStatus.AWAITING_OPERATOR.value
    assert s.verdict_status is None


def test_queued_message_emits_messages_delivered_on_resume() -> None:
    """A message queued while the agent is busy is marked delivered when it is read (#204)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = _echo_box()
        agent = build_retest_agent(streaming(script_run_then_conclude))
        start_and_step(session, registry, s.id, agent, box, "Retest.")  # -> awaiting_command
        cid = _pending_cid(registry, s.id)
        rs.submit_message(session, registry, s.id, "please note this")  # queued while busy
        apply_decision(session, registry, s.id, approved=True, command_id=cid)  # drains + delivers
        events = rs.load_events_after(session, s.id, 0)
    delivered = [e for e in events if e["kind"] == SessionEventKind.MESSAGES_DELIVERED.value]
    human = [e for e in events if e["kind"] == SessionEventKind.HUMAN_MESSAGE.value]
    assert len(delivered) == 1
    assert delivered[0]["seq"] > human[0]["seq"]  # the marker follows the queued message


def test_run_cancellable_turn_reruns_on_unstick() -> None:
    """An operator unstick cancels the in-flight turn and re-runs it from the top (#204)."""
    live = rs.LiveSession(
        agent=build_retest_agent(streaming(script_await_operator)), sandbox=_echo_box()
    )
    calls = {"n": 0}
    sentinel = object()

    async def drive() -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            live.abort_retry = True  # stands in for request_restart() having fired
            raise asyncio.CancelledError
        return sentinel

    result = rs._run_cancellable_turn(drive, session_id=1, channel=DeltaChannel(), live=live)
    assert result is sentinel
    assert calls["n"] == 2  # the turn ran twice: the wedged one, then the retry


def test_run_cancellable_turn_raises_on_teardown_cancel() -> None:
    """A cancel that was not an unstick surfaces as _TurnAbortedError, not a retry (#204)."""
    live = rs.LiveSession(
        agent=build_retest_agent(streaming(script_await_operator)), sandbox=_echo_box()
    )

    async def drive() -> Any:
        raise asyncio.CancelledError

    with pytest.raises(rs._TurnAbortedError):
        rs._run_cancellable_turn(drive, session_id=1, channel=DeltaChannel(), live=live)


def test_live_session_cancel_primitives_no_op_without_a_turn() -> None:
    """With no turn attached, the cancel primitives report nothing to do (#204)."""
    live = rs.LiveSession(
        agent=build_retest_agent(streaming(script_await_operator)), sandbox=_echo_box()
    )
    assert live.request_restart() is False
    assert live.request_cancel() is False
    assert live.consume_restart() is False


def test_request_restart_schedules_cancel_on_an_attached_turn() -> None:
    """request_restart cancels the attached task cross-loop and flags a retry (#204)."""
    live = rs.LiveSession(
        agent=build_retest_agent(streaming(script_await_operator)), sandbox=_echo_box()
    )
    loop = asyncio.new_event_loop()

    async def forever() -> None:
        await asyncio.sleep(3600)

    task = loop.create_task(forever())
    live.attach_run(loop, task)
    assert live.request_restart() is True
    assert live.consume_restart() is True  # the retry flag was set
    with contextlib.suppress(asyncio.CancelledError):
        loop.run_until_complete(task)
    assert task.cancelled()
    live.detach_run()
    loop.close()


def test_restart_model_no_op_when_not_live() -> None:
    """restart_model does nothing (no TURN_RESTARTED marker) when the session is not live (#204)."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        rs.restart_model(session, registry, s.id)  # no live session -> no-op
        events = rs.load_events_after(session, s.id, 0)
    assert not [e for e in events if e["kind"] == SessionEventKind.TURN_RESTARTED.value]
