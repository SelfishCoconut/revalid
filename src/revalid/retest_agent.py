"""FR-17 / M6 agentic retest agent (ADR-0025, Slice 0).

One gated ``run_command`` tool (Pydantic AI deferred approval) + a
``ConcludeOutput`` structured verdict. The orchestrator (retest_session.py)
runs the agent step-by-step, pausing on each proposed command for human
approval and resuming with ``ToolApproved``/``ToolDenied``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import KnownModelName, Model

from revalid.domain import VerdictStatus
from revalid.llm import resolve_model
from revalid.sandbox import TIMEOUT_EXIT_CODES, CommandResult, Sandbox

_MAX_TOOL_RETRIES = 2

#: Per-command wall-clock cap when the agent does not choose one. Kept short so a
#: quick probe (curl) never lingers; the agent raises it explicitly for slow tools.
DEFAULT_COMMAND_TIMEOUT = 30
#: Hard ceiling on the agent-chosen per-command timeout. The model picks how long
#: each command may run (a scan needs longer than a curl), but cannot ask for an
#: unbounded wait — a runaway or hanging command is always killed by this bound.
MAX_COMMAND_TIMEOUT = 300


def clamp_timeout(seconds: int) -> int:
    """Clamp an agent-requested per-command timeout to ``[1, MAX_COMMAND_TIMEOUT]``."""
    return max(1, min(seconds, MAX_COMMAND_TIMEOUT))


_BASE_INSTRUCTIONS = """\
You are a penetration-test *retester*. You are given one finding to re-verify \
against an authorised lab target that is reachable from your sandbox.

Rules:
- Work one command at a time. Propose a single shell command plus a \
one-line rationale; a human approves or rejects each before it runs.
- The sandbox can reach ONLY the lab target — never the internet or the host.
- Every command must be NON-INTERACTIVE and self-terminating: never wait on \
stdin, and never background a command (no trailing `&`) — you only see output \
once it exits.
- You decide how long each command may run: choose `timeout_seconds` to fit it. \
A quick probe (curl) needs only a few seconds; a port scan or fuzzing run may \
need 60-180. The command is killed if it overruns; if you see it "timed out", \
either raise the limit or narrow the scope (e.g. scan fewer ports) — a bounded \
`nmap -Pn -T4 --top-ports 100` beats a full-range sweep that never finishes.
- Prefer non-destructive verification. Do not attempt to damage the target.
- If you have genuinely exhausted the options you can think of and still cannot \
determine the outcome, conclude `inconclusive` — this does NOT end the session; \
it hands back to the operator. In the rationale, say what you tried and exactly \
what guidance or access you need. Only the operator records a final `inconclusive`.
- The operator is in charge. When they message you, that message is your \
priority — read what they actually said and answer *that*. The goal is background \
context, not a script to rush through; do not ignore a message and press on toward \
the goal.
- If the operator's message is conversational — a greeting, small talk, a simple \
question you can answer without running anything, or just acknowledging you — \
reply with a short `AwaitOperator` message (e.g. "Hi — ready when you are.") and \
STOP. Do NOT run a command or conclude just to make progress: hand control back \
and wait for them.
- Run a command only to make genuine retest progress, and when you do, fold in \
whatever the operator asked for. To reply and keep working in the same turn, use \
`respond` for the note and then propose your command. Use `respond` sparingly — a \
brief answer or status note, never step-by-step narration.
"""

#: Appended when the operator has handed over the wheel (Auto-run / free-launch,
#: ADR-0039): the agent drives itself to a verdict, chaining commands as needed.
_AUTONOMOUS_GUIDANCE = """\
You are running AUTONOMOUSLY: the operator turned Auto-run on and handed you the \
wheel. Keep going on your own — reason, run a command, observe its output, and \
continue — until you can make a determination. When you are confident, conclude \
`still_open` (the issue reproduces) or `fixed` (it does not). That ends the session.
"""

#: Appended in the default guided mode (ADR-0039): the operator drives one step at
#: a time, so the agent does a single action and hands back rather than racing to a
#: verdict. The orchestrator enforces the stop; these instructions shape a *useful*
#: hand-back (a recommendation for the operator, not a silent stall).
_GUIDED_GUIDANCE = """\
You are being GUIDED by the operator, one step at a time. Do exactly what they \
ask — normally a single command — then STOP and hand back: after a command runs, \
briefly note what it showed and either recommend the single next command, or, if \
you now believe you know the outcome, recommend a determination (`still_open` or \
`fixed`) and why. Do NOT chain more commands on your own, and do NOT record the \
verdict yourself — the operator sets the pace and makes the final call. They will \
tell you the next move, take over, or turn on Auto-run to hand you the wheel.
"""


class ConcludeOutput(BaseModel):
    """The agent's terminal verdict for a retest session."""

    model_config = ConfigDict(frozen=True)

    status: VerdictStatus
    rationale: str = Field(min_length=1)


class AwaitOperator(BaseModel):
    """The agent replied and is handing control back to the operator (issue #204).

    The turn ends without a command or a verdict: the agent answered the operator
    conversationally (a greeting, a small-talk reply, an acknowledgement) and is
    now waiting for them, sandbox kept alive. Lighter than an ``inconclusive``
    conclusion — it is not "I'm stuck, please guide me", just "your move". The
    orchestrator surfaces ``message`` as an agent chat bubble and parks the session
    in ``awaiting_operator``; the operator's next message resumes it.
    """

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1)


def _no_observations() -> list[str]:
    """Default ``drain_observations``: no operator activity to surface."""
    return []


def _no_emit_message(message: str) -> None:
    """Default ``emit_message``: drop agent prose (agent-unit tests need no sink)."""


@dataclass
class RetestSessionDeps:
    """Runtime dependencies injected into the retest agent's tools."""

    sandbox: Sandbox
    emit_output: Callable[[str, CommandResult], None]
    #: Returns (and clears) any manual operator commands run since the agent's
    #: last turn, so the agent observes what the human did (FR-17 Slice 2). The
    #: default surfaces nothing — the human-command path (`!`) injects the real
    #: drain via the orchestrator's :func:`~revalid.retest_session._make_deps`.
    drain_observations: Callable[[], list[str]] = _no_observations
    #: Records the agent's prose replies to the operator (FR-17 Slice 4). Invoked
    #: by the non-gated ``respond`` tool; the orchestrator wires this to append an
    #: ``agent_message`` transcript event. The default drops it (agent-unit tests).
    emit_message: Callable[[str], None] = _no_emit_message
    #: Whether the operator has handed over the wheel (Auto-run / free-launch). It
    #: selects the agent's persona via dynamic instructions (ADR-0039): guided
    #: (one action then hand back) when ``False``, autonomous (drive to a verdict)
    #: when ``True``. Rebuilt fresh each turn from the live session, so a live
    #: Auto-run toggle takes effect on the agent's next turn. Default ``False`` —
    #: the guided persona — so agent-unit constructions need not set it.
    free_launch: bool = False


def _format_result(result: CommandResult) -> str:
    """Render a command result as the tool-return text the model observes."""
    return (
        f"exit_code={result.exit_code} elapsed_ms={result.elapsed_ms}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def format_observations(observations: list[str]) -> str:
    """Render buffered operator activity as a block the agent reads on its next turn.

    Returns an empty string when there is nothing to surface, so callers can
    append it unconditionally.

    Args:
        observations: Human-run command summaries buffered since the last turn.

    Returns:
        A labelled block to append to the next tool result, or ``""`` if empty.
    """
    if not observations:
        return ""
    return "\n\n--- operator activity while you waited ---\n" + "\n".join(observations)


#: The retest agent's output union: a terminal verdict, a conversational hand-back
#: (issue #204), or a gated command awaiting approval. Named once so the
#: orchestrator's many step-site annotations stay in sync (mypy --strict).
RetestOutput = ConcludeOutput | AwaitOperator | DeferredToolRequests
#: The built retest agent's type — an :class:`~pydantic_ai.Agent` over the deps and
#: the :data:`RetestOutput` union. Shared by the orchestrator and the app layer.
RetestAgent = Agent[RetestSessionDeps, RetestOutput]


def build_retest_agent(
    model: Model | KnownModelName | str | None = None,
) -> RetestAgent:
    """Build the FR-17 retest agent: a gated ``run_command`` tool + a verdict.

    Args:
        model: A Pydantic AI model instance or name. When omitted, the
            configured backend is used (``REVALID_LLM_MODEL``, Claude by
            default — FR-13); tests pass ``TestModel``/``FunctionModel``.

    Returns:
        An agent whose output is a :class:`ConcludeOutput` verdict, an
        :class:`AwaitOperator` conversational hand-back, or — while a gated
        ``run_command`` call awaits human approval — a
        :class:`~pydantic_ai.DeferredToolRequests`.
    """
    agent: RetestAgent = Agent(
        model if model is not None else resolve_model(),
        deps_type=RetestSessionDeps,
        output_type=[ConcludeOutput, AwaitOperator, DeferredToolRequests],
        instructions=_BASE_INSTRUCTIONS,
        retries=_MAX_TOOL_RETRIES,
        defer_model_check=True,
    )

    @agent.instructions
    def _mode_guidance(ctx: RunContext[RetestSessionDeps]) -> str:
        """Append the persona for this turn's mode (ADR-0039).

        Evaluated per run against freshly-built deps, so a live Auto-run toggle
        switches the agent between driving itself to a verdict (autonomous) and
        doing one action then handing back (guided) on its very next turn.
        """
        return _AUTONOMOUS_GUIDANCE if ctx.deps.free_launch else _GUIDED_GUIDANCE

    @agent.tool(requires_approval=True)
    def run_command(
        ctx: RunContext[RetestSessionDeps],
        command: str,
        rationale: str,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> str:
        """Run one shell command in the egress-locked sandbox and return its output.

        Args:
            ctx: The run context carrying the sandbox + output-emit callback.
            command: The exact shell command to execute (lab target only).
            rationale: A one-line reason this command advances the retest.
            timeout_seconds: How long the command may run before it is killed —
                pick a value that fits it (a few seconds for a curl, more for a
                scan). Clamped to at most ``MAX_COMMAND_TIMEOUT`` seconds.

        Returns:
            The command's exit code, timing, stdout and stderr as text; a note is
            appended when the command was killed for exceeding its timeout.
        """
        timeout = clamp_timeout(timeout_seconds)
        result = ctx.deps.sandbox.exec(command, timeout=timeout)
        ctx.deps.emit_output(command, result)
        text = _format_result(result)
        if result.exit_code in TIMEOUT_EXIT_CODES:
            text += f"\n[terminated: the command exceeded its {timeout}s timeout]"
        return text + format_observations(ctx.deps.drain_observations())

    @agent.tool
    def respond(ctx: RunContext[RetestSessionDeps], message: str) -> str:
        """Send a short prose message to the operator (e.g. answer a question).

        Use this to reply to the operator or give a brief status note — not to
        narrate every step. It runs nothing; after it you continue with your
        plan, a command, or a verdict.

        Args:
            ctx: The run context carrying the message-emit callback.
            message: The prose to show the operator in the chat.

        Returns:
            A short confirmation the message was delivered.
        """
        ctx.deps.emit_message(message)
        return "Delivered to the operator."

    return agent


_QA_INSTRUCTIONS = """\
You are the penetration-test retester, replying in chat to the operator's question \
about the retest in progress. Answer concisely and specifically, using only the \
context you are given (the finding, the target scope, the current goal, and what has \
happened so far). If the answer is not in the context, say so briefly. This is a \
chat reply only — do NOT propose or run commands here.
"""


def build_qa_agent(model: Model | KnownModelName | str | None = None) -> Agent[None, str]:
    """Build a lightweight prose Q&A agent for answering operator questions (FR-17).

    Decoupled from the retest loop: it runs no tools and never touches the deferred
    command state, so it can answer at any time — including while the main agent is
    mid-turn — from a read-only view of the transcript.

    Args:
        model: A Pydantic AI model instance or name; the configured backend when
            omitted (tests pass ``TestModel``/``FunctionModel``).

    Returns:
        An agent whose output is the plain-text answer to show the operator.
    """
    return Agent(
        model if model is not None else resolve_model(),
        output_type=str,
        instructions=_QA_INSTRUCTIONS,
        defer_model_check=True,
    )
