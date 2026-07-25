# Architecture — data model

> Authored page: this does NOT auto-sync with code. A PR that adds a table,
> a column or a lifecycle state described here must update it in the same PR
> (checked by the `doc-curator` agent). The generated
> [UML class diagrams](../reference/uml.md) show the Python classes; this page
> shows the **persisted** shape and the lifecycles that move through it.

Durable state is SQLite via SQLAlchemy (`db.py`), created with `create_all` —
there are no migrations, by decision (ADR-0002/0008): a single-user local tool
with a disposable database gets `make reset-db`, not Alembic.

## Entity relationships

Two views of the same schema. The overview below fixes the relationships and
their cardinalities; the detailed diagram after it adds every column. The
overview is what the thesis reproduces — the full attribute listing is a
reference artefact, unreadable at print size.

<!-- thesis-fig: data-model -->
```mermaid
erDiagram
    REPORTS ||--o{ FINDINGS : "yields"
    FINDINGS ||--|{ FINDING_VERSIONS : "append-only lineage"
    FINDINGS ||--o{ FINDING_NOTES : "stage-tagged notes"
    FINDINGS ||--o{ RETEST_SESSIONS : "retest attempts"
    FINDINGS ||--o{ VERDICTS : "determinations"
    RETEST_SESSIONS ||--|{ SESSION_EVENTS : "append-only transcript"
    RETEST_SESSIONS ||--o{ VERDICTS : "concluded by"
    CHAT_SESSIONS ||--|{ CHAT_MESSAGES : "thread"
    SETTINGS {
        int id PK
    }
```

### Full attribute listing

```mermaid
erDiagram
    REPORTS ||--o{ FINDINGS : "yields"
    FINDINGS ||--|{ FINDING_VERSIONS : "append-only lineage"
    FINDINGS ||--o{ FINDING_NOTES : "stage-tagged annotations"
    FINDINGS ||--o{ RETEST_SESSIONS : "one per retest attempt"
    FINDINGS ||--o{ VERDICTS : "determinations"
    RETEST_SESSIONS ||--|{ SESSION_EVENTS : "append-only transcript"
    RETEST_SESSIONS ||--o{ VERDICTS : "concluded by"
    CHAT_SESSIONS ||--|{ CHAT_MESSAGES : "thread"

    REPORTS {
        int id PK
        string filename
        string status "extracting-ready-failed-cancelled"
        string model "LLM used for extraction"
        string error "set only on failed / cancelled"
        int finding_count
        bool archived
        string content_hash "dedup of re-uploads"
        json doc_metadata "best-effort, never fails a report"
        datetime created_at
    }
    FINDINGS {
        int id PK
        int report_id FK "null for manual entry"
        datetime created_at
    }
    FINDING_VERSIONS {
        int id PK
        int finding_id FK
        int version "1 = extraction"
        string origin "extraction-or-edit"
        string title
        string severity
        string description
        string impact
        string attack_vector
        json affected_endpoints
        json reproduction_steps
        json cvss "vector, base_score, inferred (FR-19)"
        json mitre "technique ids, inferred (FR-19)"
        json raw "verbatim source entry"
        string edited_by
        string reason "why the operator changed it"
        datetime created_at
    }
    FINDING_NOTES {
        int id PK
        int finding_id FK
        string stage "extract-goal-retest-verdict-general (+legacy plan-approve)"
        string body
        string author
        datetime created_at
    }
    RETEST_SESSIONS {
        int id PK
        int finding_id FK
        string status "see lifecycle below"
        string model
        string verdict_status
        string verdict_rationale
        bool free_launch
        datetime created_at
        datetime ended_at
    }
    SESSION_EVENTS {
        int id PK
        int session_id FK
        int seq "monotonic per session"
        string kind "17 event kinds"
        json payload
        datetime created_at
    }
    VERDICTS {
        int id PK
        int finding_id FK
        int session_id FK
        string status "still_open-fixed-inconclusive"
        string reason_code
        string rationale
        json matched_indicators
        json evidence "AgenticEvidence, from the transcript"
        string actor "agent-or-operator"
        datetime created_at
    }
    SETTINGS {
        int id PK
        string model "the one FR-13 switch"
        string base_url
        string api_key
        datetime updated_at
    }
    CHAT_SESSIONS {
        int id PK
        string title
        string model
        datetime created_at
        datetime updated_at
    }
    CHAT_MESSAGES {
        int id PK
        int chat_id FK
        string role "user-or-assistant"
        string content
        datetime created_at
    }
```

Three properties of this schema carry most of the design weight:

- **A finding is an identity, not a row of content.** `findings` holds only the
  identity and its report link; every field a human reads lives in
  `finding_versions`. That is what makes FR-16's lineage possible.
- **`session_events` is the source of truth for a session**, and the verdict row
  is a *derivation* of it. FR-10's audit re-projects the transcript and diffs it
  against `verdicts` — which only means something because the transcript is
  append-only and independently written.
- **`settings` is a single row.** One authoritative model/provider selection,
  seeded once from the environment and thereafter runtime-editable (ADR-0021).

## Finding version lineage (FR-16, ADR-0024)

Findings are versioned, never overwritten. Version 1 is `extraction` — what the
machine proposed. Every operator correction appends an `edit`.

<!-- thesis-fig: version-lineage -->
```mermaid
flowchart LR
    A["v1 — extraction<br/>what the machine proposed"]
    B["v2 — edit<br/>reason: wrong endpoint"]
    C["v3 — edit<br/>reason: severity overstated"]
    D(["current = highest version"])
    A --> B --> C --> D

    F(["finding identity<br/>(findings row)"])
    F --- A
    N1["note @ extract"] --> F
    N2["note @ goal"] --> F
    N3["note @ retest"] --> F
    N4["note @ verdict"] --> F

    style A fill:#e7f5ff,stroke:#1971c2
    style D fill:#ebfbee,stroke:#2f9e44
    style F fill:#fff9db,stroke:#f08c00
```

The evaluation depends on this: scoring "did the model get it right" requires
knowing what the model actually said, *after* a human has corrected it.

Notes are tagged with the stage they were written on: `extract`, `goal`,
`retest`, `verdict`, or `general` for a note left from the finding overview
rather than any one stage. The enum still *reads* the retired batch path's `plan`
and `approve` values so an older database loads, but nothing writes them — the
goal stage tagged its notes `plan` until issue #113, and those rows are renamed
in place by an idempotent backfill when the engine opens
(`db._backfill_note_stages`).

## Retest session lifecycle (FR-17, ADR-0034/0042)

Five live states, one agent. A turn — the LLM call *and* any command it runs —
happens inside `working`; there is no separate `running_command`, because the
command executes within the working turn (ADR-0042). The old `thinking`,
`starting` and `running_command` states collapsed into `working`, and
`needs_guidance` folded into `awaiting_operator`.

<!-- thesis-fig: session-lifecycle -->
```mermaid
stateDiagram-v2
    direction LR
    [*] --> working: launch
    [*] --> idle: deferred / Restart

    idle --> working: Start

    working --> awaiting_command: proposes a command
    awaiting_command --> working: approve
    awaiting_command --> working: reject / message

    working --> awaiting_operator: agent hands back
    awaiting_operator --> working: operator replies

    working --> concluded: concludes (Auto-run only)
    working --> stopped: Stop
    stopped --> working: Resume

    awaiting_operator --> concluded: operator concludes
    awaiting_command --> concluded: operator concludes
    stopped --> concluded: operator concludes
    working --> concluded: operator concludes (any live state, ADR-0046)

    working --> working: Restart model
    working --> error: unhandled failure
    working --> ended: operator ends it

    concluded --> idle: reopen — verdict withdrawn
    concluded --> [*]
    ended --> [*]
    error --> [*]

    note right of awaiting_operator
        Non-terminal: the sandbox stays alive.
        Guided mode parks here after every
        approved action, and the agent never
        self-records a verdict — only the
        operator concludes (ADR-0034/0040/0042).
        The console renders no prompt here: it
        waits, and Conclude is always to hand
        (ADR-0046).
    end note
```

The transitions, in full — the diagram keeps its labels short so it stays legible
when it is scaled into the memoir:

| Transition | What it is |
|---|---|
| `idle → working` | The operator presses **Start**, or sends a message. The sandbox is provisioned at the top of that first turn. |
| `working → awaiting_command` | The agent proposed a `run_command`; the deferred-tool gate suspended the run. |
| `awaiting_command → working` (approve) | The command runs **inside** the next turn — there is no separate `running_command` state. |
| `awaiting_command → working` (reject / message) | A rejection resumes the agent with `ToolDenied`; a message **withdraws** the proposal and re-runs the agent with it (ADR-0042). |
| `working → awaiting_operator` | The agent handed back: a reply, a guided one-action report, a verdict recommendation, or "I'm out of options". |
| `working → working` | **Restart model** — the operator aborts a wedged in-flight turn and has it re-run (ADR-0039). |
| `working → concluded` | The agent recorded its own verdict. Reachable **only** under Auto-run. |
| `* → concluded` (operator) | **Conclude** is a permanent control (ADR-0046): the operator records their own verdict from **any** live state, including mid-turn and at the approval gate. No state grants or withholds it. |
| `concluded → idle` | **Reopen** (ADR-0043): the operator withdraws the recorded verdict and keeps testing. The only edge out of a terminal state. |

A verdict the agent authors itself (`working --> concluded`) is reachable **only
under free launch / Auto-run**; in guided mode the agent never self-concludes and
never self-records `inconclusive` — it hands back through `awaiting_operator` and
lets the operator conclude (ADR-0034/0040). `given_up` exists in the enum but is
**retired** — kept only so any legacy row stays terminal. Nothing writes it.

## Transcript event kinds

Every one of these is a numbered `session_events` row. Together they are the
replayable record NFR-02 relies on.

```mermaid
flowchart TB
    subgraph agent["the agent's voice"]
        A1["agent_message"]
        A2["command_proposed"]
        A3["verdict"]
        A1 ~~~ A2 ~~~ A3
    end
    subgraph human["the operator's voice"]
        H1["command_approved"]
        H2["command_rejected"]
        H3["human_command"]
        H4["human_message"]
        H5["verdict_adjudicated"]
        H6["plan_updated — the user-owned goal"]
        H7["verdict_cancelled — reopened (ADR-0043)"]
        H1 ~~~ H2 ~~~ H3 ~~~ H4 ~~~ H5 ~~~ H6 ~~~ H7
    end
    subgraph system["the system's voice"]
        S1["command_output"]
        S2["state_change"]
        S3["target_set — scope, emitted once"]
        S5["free_launch_changed"]
        S6["error"]
        S7["messages_delivered — queued msg read (ADR-0039)"]
        S8["turn_restarted — operator unstick (ADR-0039)"]
        S1 ~~~ S2 ~~~ S3 ~~~ S5 ~~~ S6 ~~~ S7 ~~~ S8
    end

    A3 --> T[("session_events<br/>append-only, seq-ordered")]
    H5 --> T
    H7 --> T
    S2 --> T
    T --> V["verdict row<br/>a derivation, not the source"]
    T --> AU["FR-10 audit<br/>re-projects and diffs"]
    T --> EX["FR-12 export<br/>schema 1.5"]

    style T fill:#fff4e6,stroke:#e8590c
    style V fill:#ebfbee,stroke:#2f9e44
```

There is no `needs_guidance` event kind (removed in ADR-0042): an agent hand-back
to `awaiting_operator` is recorded like any other turn — an `agent_message`
carrying its words plus a `state_change` — so the transcript needs no special
"stuck" marker.

`verdict_adjudicated` is the operator's override. Once present, the **latest**
one is the authoritative event for audit purposes — not the agent's original
`verdict`.

`verdict_cancelled` is its counterpart for a **reopened** session (ADR-0043): the
operator withdrew the determination rather than replacing it. Note what the pair
of them implies about where truth lives — reopening *deletes* the row in
`verdicts`, because that table is a projection of current determinations, but it
cannot delete anything from `session_events`, because that is the record the
projection was derived from. A verdict can be retracted; the fact that it was
once reached cannot.

## Report ingest lifecycle

```mermaid
stateDiagram-v2
    direction LR
    [*] --> extracting: POST /api/reports (PDF)
    extracting --> ready: findings persisted
    extracting --> failed: PdfError or any exception
    extracting --> cancelled: operator stops it mid-run (ADR-0039)
    ready --> [*]
    failed --> [*]
    cancelled --> [*]

    note right of extracting
        Only the PDF door is asynchronous.
        JSON import and manual entry land
        directly on ready.
        run_extraction guarantees the report
        always leaves extracting, so the SPA
        status poll is guaranteed to terminate.
        A cancelled report keeps whatever was
        extracted before the stop, so it stays
        re-runnable or deletable.
    end note
```
