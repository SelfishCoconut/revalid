"""Unit tests for the FR-17 retest-session persistence layer (ADR-0025, Slice 0).

In-memory SQLite, no I/O. Covers the append-only transcript (monotonic ``seq``),
status transitions, and verdict recording on :class:`~revalid.db.RetestSessionRecord`,
plus the Task 5 orchestration layer that drives the Task 4 agent step-by-step.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.orm import Session
from tests._retest_helpers import (
    has_command_result,
    script_always_propose,
    script_run_then_conclude,
)

from revalid import retest_session as rs
from revalid.db import IN_MEMORY, create_db_engine, session_factory
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


def test_create_session_starts_in_starting_status() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        assert s.id is not None
        assert s.finding_id == fid
        assert s.status == RetestSessionStatus.STARTING.value
        assert s.model == "ollama:qwen3.6:27b"
        assert s.verdict_status is None
        assert s.ended_at is None


def test_append_event_assigns_monotonic_seq() -> None:
    sessions = session_factory(create_db_engine(IN_MEMORY))
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="ollama:qwen3.6:27b")
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "starting"})
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
        rs.append_event(session, s.id, SessionEventKind.STATE_CHANGE, {"to": "starting"})
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


def test_full_cycle_proposes_runs_and_concludes() -> None:
    """start_and_step pauses on the proposed command; approving runs it and concludes."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([CommandResult(stdout="{token}", stderr="", exit_code=0, elapsed_ms=5)])
        agent = build_retest_agent(FunctionModel(script_run_then_conclude))

        start_and_step(session, registry, s.id, agent, box, "Retest the SQLi finding.")
        session.refresh(s)
        assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
        assert "command_proposed" in kinds

        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
        assert s.status == RetestSessionStatus.CONCLUDED.value
        assert s.verdict_status == "still_open"
        assert box.commands and box.stopped  # ran once, torn down

        events_before = rs.load_events_after(session, s.id, 0)
        # A repeat decision with the same (now-consumed) cid must no-op: the live
        # session was already torn down on conclude, so no command runs twice and
        # no extra transcript event is appended (final-review Fix 1).
        apply_decision(session, registry, s.id, approved=True, command_id=cid)
        session.refresh(s)
        events_after = rs.load_events_after(session, s.id, 0)
    assert len(box.commands) == 1  # still exactly one execution, not two
    assert events_after == events_before
    assert s.status == RetestSessionStatus.CONCLUDED.value


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
        agent = build_retest_agent(FunctionModel(script_run_then_conclude))

        start_and_step(session, registry, s.id, agent, box, "Retest.")
        apply_decision(session, registry, s.id, approved=True, command_id="not-the-pending-id")
        session.refresh(s)
    assert box.commands == []
    assert s.status == RetestSessionStatus.AWAITING_COMMAND.value
    live = registry.get(s.id)
    assert live is not None and live.pending_call_id is not None  # still pending, untouched


def test_apply_decision_reject_never_executes_and_still_concludes() -> None:
    """Denying the proposed command never touches the sandbox but still resumes the run."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox([])  # nothing scripted: must never be called
        agent = build_retest_agent(FunctionModel(script_run_then_conclude))

        start_and_step(session, registry, s.id, agent, box, "Retest.")
        cid = _pending_cid(registry, s.id)
        apply_decision(
            session, registry, s.id, approved=False, reason="out of scope host", command_id=cid
        )
        session.refresh(s)
        kinds = [e["kind"] for e in rs.load_events_after(session, s.id, 0)]
    assert "command_rejected" in kinds
    assert box.commands == []
    assert s.status == RetestSessionStatus.CONCLUDED.value


def test_budget_exhaustion_gives_up() -> None:
    """An always-proposing agent is bounded: exceeding max_steps forces a give-up.

    ``max_steps=1`` allows exactly one approved command. The first approval runs
    it; the second approval would be a second command, which exceeds the budget
    and must force-conclude ``inconclusive`` + ``GIVEN_UP`` without running it.
    """
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        box = FakeSandbox(
            lambda cmd: CommandResult(stdout="", stderr="", exit_code=0, elapsed_ms=1)
        )
        agent = build_retest_agent(FunctionModel(script_always_propose))  # never concludes

        start_and_step(session, registry, s.id, agent, box, "Retest.", max_steps=1)
        cid1 = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid1)  # the allowed step
        assert len(box.commands) == 1

        cid2 = _pending_cid(registry, s.id)  # a fresh proposal after the first ran
        apply_decision(session, registry, s.id, approved=True, command_id=cid2)  # over budget
        session.refresh(s)
    assert s.status == RetestSessionStatus.GIVEN_UP.value
    assert s.verdict_status == "inconclusive"
    assert len(box.commands) == 1  # the over-budget command never ran
    assert box.stopped


def test_is_terminal_matches_the_terminal_statuses() -> None:
    assert rs.is_terminal(RetestSessionStatus.CONCLUDED)
    assert rs.is_terminal(RetestSessionStatus.GIVEN_UP)
    assert rs.is_terminal(RetestSessionStatus.ENDED)
    assert rs.is_terminal(RetestSessionStatus.ERROR)
    assert not rs.is_terminal(RetestSessionStatus.STARTING)
    assert not rs.is_terminal(RetestSessionStatus.AWAITING_COMMAND)
    assert not rs.is_terminal(RetestSessionStatus.RUNNING_COMMAND)


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
        agent = build_retest_agent(FunctionModel(script_always_propose))
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
        agent = build_retest_agent(FunctionModel(_always_boom))

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
        agent = build_retest_agent(FunctionModel(_propose_then_boom))
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
        agent = build_retest_agent(FunctionModel(script_run_then_conclude))
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
        agent = build_retest_agent(FunctionModel(_conclude_noting_operator))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_human_command(session, registry, s.id, "whoami")  # while awaiting approval
        cid = _pending_cid(registry, s.id)
        apply_decision(session, registry, s.id, approved=True, command_id=cid)

        session.refresh(s)
    assert s.status == RetestSessionStatus.CONCLUDED.value
    assert s.verdict_rationale == "saw-operator"  # the agent read the operator activity


def test_reject_folds_operator_activity_into_the_denial() -> None:
    """A rejection surfaces buffered operator activity to the agent via the denial message."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        fid = _seed_finding(session)
        s = rs.create_session(session, finding_id=fid, model="m")
        agent = build_retest_agent(FunctionModel(_conclude_noting_operator))
        start_and_step(session, registry, s.id, agent, _echo_box(), "Retest.")

        rs.submit_human_command(session, registry, s.id, "whoami")
        cid = _pending_cid(registry, s.id)
        # Reject the agent's own proposal: no command runs, but the denial the
        # agent reads still carries what the operator did.
        apply_decision(session, registry, s.id, approved=False, reason="not that", command_id=cid)

        session.refresh(s)
    assert s.verdict_rationale == "saw-operator"


def test_submit_human_command_on_dead_session_is_a_noop() -> None:
    """Submitting a command to a non-live session records nothing and does not raise."""
    sessions = session_factory(create_db_engine(IN_MEMORY))
    registry = SessionRegistry()
    with sessions() as session:
        rs.submit_human_command(session, registry, 999, "whoami")  # never started
        assert rs.load_events_after(session, 999, 0) == []
