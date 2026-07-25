# revalid

**AI-Driven System for the Revalidation of Pentest Findings** — Bachelor's Thesis (TFG), ESII-UCLM, by Álvaro Navarro.

A pentest report goes in. Each finding it describes is re-verified against an
**authorised lab target** by an LLM agent that cannot run a single command
without a human saying yes. What comes out is a verdict — `fixed` or
`still_open` — backed by the real output of the command that decided it.

!!! info "Human-in-the-loop, by construction"
    The agent is not *asked* to behave. Every command it proposes is a Pydantic
    AI deferred tool call that **cannot resolve** into an execution until the
    operator approves it (ADR-0025) — and what an approved command can reach is
    decided by the shape of the network it runs in, not by inspecting it. A lab
    retest gets a per-session Docker network with `internal=True`: no route to
    the host, no route to the internet, only the target the operator attached.
    An online one gets a per-session L3 egress gateway whose firewall the sandbox
    holds no capability to change (ADR-0045). Both are drawn on the
    [network topology](architecture/topology.md) page.

## Start here

| | |
|---|---|
| **[How it works](architecture/workflow.md)** | The program narrative: what happens, in what order, and who is in control at each step. **Read this first.** |
| [C4 model](architecture/c4.md) | Context, container and component diagrams, plus sequence diagrams for the wire-level detail. |
| [Network topology](architecture/topology.md) | How FR-06 containment is actually built: both sandbox topologies, the egress ruleset, per-session lifecycle, and the limits of the guarantee. |
| [Class model](architecture/class-model.md) | Curated class diagrams: the domain core, the persistence seam, the agentic session collaboration, the export document. |
| [Data model](architecture/data-model.md) | The persisted schema as an ER diagram, plus the lifecycles that move through it — session states, finding lineage, transcript events. |
| [Subsystem flows](architecture/subsystem-flows.md) | Everything around the retest spine: the three ingest doors, corpus chat, model resolution, the SPA route map. |
| [Requirements (SRS)](requirements/srs.md) | The FR/NFR catalogue driving the Kanban board, with per-requirement acceptance criteria. |
| [Use-case model](requirements/use-cases.md) | Actors, use cases traced to requirements, and the decisive scenario expanded with its exception paths. |
| [ADRs](adr/README.md) | The decision log (MADR). A decision without an ADR doesn't exist. |
| [API reference](reference/api.md) | Generated from docstrings by mkdocstrings — edit the code, not the page. |
| [UML](reference/uml.md) | Package dependencies plus a class diagram per group of modules, regenerated from the code by `pyreverse` on every build. |
| [Roadmap](roadmap.md) | Current state, milestone plan and next action — the durable resume point. |
| [Working on revalid](development-plan.md) | Environment, commands, test pyramid and the contribution workflow. |
| [AI usage](ai-usage/AI_USAGE_LOG.md) | Public audit trail (Reglamento TFG 2026 §6). |

## What it does

**Get findings in — three doors, one destination.** A PDF report (pdfplumber
extraction, then LLM structuring — FR-01/FR-03), a DefectDojo-style JSON export
(pure schema mapping, no LLM — FR-02), or manual entry (the escape hatch when a
model cannot reliably ingest a report — ADR-0020). All three land on a `ready`
report with findings attached, so everything downstream is identical. Every
finding is enriched with a CVSS code and a MITRE ATT&CK mapping, inferred and
flagged as such when the source report is silent (FR-19, ADR-0037).

**Correct what the machine got wrong.** Findings are versioned, never
overwritten: version 1 is what the model proposed, and each operator correction
appends an `edit` version (FR-16, ADR-0024). The lineage of "what the model
said" versus "what the human fixed" survives — which is what makes the
evaluation honest.

**Retest interactively.** The operator sets the goal, launches a session, and
watches the agent work in a chat-centric console with a docked terminal: the
agent proposes a command, the operator approves, rejects with a reason, or types
their own — and the agent observes the result on its next turn (FR-17). Every
proposal, approval, rejection, command output, operator message and verdict is a
numbered row in an append-only transcript.

**Refuse to guess.** An agent that has run out of ideas does not get to say
"fixed". `inconclusive` is never written as a verdict — the session parks in
`awaiting_operator`, keeps the sandbox alive, and hands back to the operator
(ADR-0034/0042). In the default guided mode the agent does not self-conclude at
all: even a confident `fixed` comes back as a *recommendation* for the operator
to confirm (ADR-0040).

**Derive off the trail.** The audit re-projects each session's transcript and
diffs it against the stored verdict row (FR-10); the export assembles a whole
run into one schema-versioned document, validated against a schema generated
from the model so it cannot drift (FR-12); the evaluation harness scores exports
against ground truth (FR-15). A **reports chat** answers questions over the
whole corpus with read-only query tools (FR-18).

## Running it

Everything runs in one `uvicorn` process bound to `127.0.0.1` (NFR-03) — FastAPI
serves the compiled React SPA at `/` and the JSON API under `/api`. No broker, no
second service, SQLite for durable state.

Deploying it needs nothing but Docker — no Python or Node toolchain
([ADR-0044](adr/0044-containerised-deployment.md)):

```bash
make deploy      # app on 127.0.0.1:8000, pinned lab on :3000
make deploy-down # stop (the database volume survives)
```

The LLM stays on your host, so no model weights are pulled into the stack. Note
that the app container mounts the host Docker socket — the retest sandbox
provisions its own networks and containers as siblings — which is
root-equivalent access to the host, accepted only under the single-operator
threat model ([ADR-0008](adr/0008-single-user-threat-model.md)).

From a checkout instead:

```bash
make lab-up   # the authorised target (Juice Shop, pinned) — required for a real retest
make run      # build the SPA if needed, serve everything on 127.0.0.1:8000
make lab-down
```

The LLM backend is one switch — Claude or a local Ollama, runtime-editable in
Settings (FR-13, ADR-0010/0021). The same code paths run against either, which
is what makes the local-versus-cloud comparison in the evaluation possible.

---

*Source: [github.com/SelfishCoconut/revalid](https://github.com/SelfishCoconut/revalid) · "THE BEER-WARE LICENSE" (Revision 42).*
