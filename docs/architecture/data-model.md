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
        string status "extracting-ready-failed"
        string model "LLM used for extraction"
        string error "set only on failed"
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
        string stage "extract-plan-approve-retest-verdict-general"
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
        string kind "15 event kinds"
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
    N2["note @ plan<br/>= the goal stage"] --> F
    N3["note @ retest"] --> F
    N4["note @ verdict"] --> F

    style A fill:#e7f5ff,stroke:#1971c2
    style D fill:#ebfbee,stroke:#2f9e44
    style F fill:#fff9db,stroke:#f08c00
```

The evaluation depends on this: scoring "did the model get it right" requires
knowing what the model actually said, *after* a human has corrected it.

Notes are tagged with the stage they were written on. The enum keeps the legacy
`plan` and `approve` values from the retired batch path — the **goal** stage
tags its notes `plan` — and `general` marks a note left from the finding
overview rather than a specific stage.

## Retest session lifecycle (FR-17, ADR-0034)

```mermaid
stateDiagram-v2
    direction LR
    [*] --> starting

    starting --> thinking: sandbox provisioned, first agent step
    starting --> ended: operator ends it
    starting --> error: provisioning failed

    thinking --> awaiting_command: proposes run_command (gated)
    awaiting_command --> running_command: operator approves
    awaiting_command --> thinking: operator rejects (ToolDenied)
    running_command --> thinking: output appended, fed back

    thinking --> needs_guidance: agent hands back (out of ideas)
    needs_guidance --> thinking: operator steers, Keep going
    needs_guidance --> concluded: operator concludes manually

    thinking --> concluded: ConcludeOutput(fixed / still_open)
    thinking --> error: unhandled failure

    concluded --> [*]
    ended --> [*]
    error --> [*]

    note right of needs_guidance
        Non-terminal, and the sandbox stays alive.
        An agent that has run out of ideas is not
        evidence that a vulnerability is fixed.
    end note
```

`given_up` exists in the enum but is **retired** — kept only so any legacy row
stays terminal. Nothing writes it.

## Transcript event kinds

Every one of these is a numbered `session_events` row. Together they are the
replayable record NFR-02 relies on.

```mermaid
flowchart TB
    subgraph agent["the agent's voice"]
        A1["agent_message"]
        A2["command_proposed"]
        A3["verdict"]
    end
    subgraph human["the operator's voice"]
        H1["command_approved"]
        H2["command_rejected"]
        H3["human_command"]
        H4["human_message"]
        H5["verdict_adjudicated"]
        H6["plan_updated — the user-owned goal"]
    end
    subgraph system["the system's voice"]
        S1["command_output"]
        S2["state_change"]
        S3["target_set — scope, emitted once"]
        S4["needs_guidance"]
        S5["free_launch_changed"]
        S6["error"]
    end

    A3 --> T[("session_events<br/>append-only, seq-ordered")]
    H5 --> T
    S2 --> T
    T --> V["verdict row<br/>a derivation, not the source"]
    T --> AU["FR-10 audit<br/>re-projects and diffs"]
    T --> EX["FR-12 export<br/>schema 1.5"]

    style T fill:#fff4e6,stroke:#e8590c
    style V fill:#ebfbee,stroke:#2f9e44
```

`verdict_adjudicated` is the operator's override. Once present, the **latest**
one is the authoritative event for audit purposes — not the agent's original
`verdict`.

## Report ingest lifecycle

```mermaid
stateDiagram-v2
    direction LR
    [*] --> extracting: POST /api/reports (PDF)
    extracting --> ready: findings persisted
    extracting --> failed: PdfError or any exception
    ready --> [*]
    failed --> [*]

    note right of extracting
        Only the PDF door is asynchronous.
        JSON import and manual entry land
        directly on ready.
        run_extraction guarantees the report
        always leaves extracting, so the SPA
        status poll is guaranteed to terminate.
    end note
```
