# Architecture — subsystem flows

> Authored page: this does NOT auto-sync with code. A PR that changes one of
> these flows must update the diagram in the same PR (checked by the
> `doc-curator` agent).

The [C4 model](c4.md) covers the retest spine end to end. This page covers the
subsystems around it — how findings get in, how the corpus chat answers, how one
setting picks every model, and how the SPA is laid out.

## Ingest — three doors, one destination

Whatever door a finding comes through, it lands as a `ready` report with
findings attached, so everything downstream is identical. Only the PDF door is
asynchronous.

```mermaid
flowchart TB
    subgraph doors["the three doors"]
        P["PDF upload&lt;br/&gt;POST /api/reports&lt;br/&gt;FR-01"]
        J["DefectDojo JSON&lt;br/&gt;POST /api/findings/import&lt;br/&gt;FR-02"]
        M["Manual entry&lt;br/&gt;POST /api/reports/manual&lt;br/&gt;ADR-0020"]
    end

    P -->|"202 + background task"| PX["pdf.read_pdf&lt;br/&gt;pdfplumber → text + candidates"]
    PX --> EX["extract.extract_report&lt;br/&gt;LLM, schema-validated gate"]
    J --> MAP["ingest — schema mapping&lt;br/&gt;no LLM at all"]
    M --> MAP

    EX --> ENR["CVSS + MITRE ATT&CK enrichment&lt;br/&gt;copied when stated, inferred + flagged when not&lt;br/&gt;FR-19, ADR-0037"]
    MAP --> ENR
    ENR --> PERSIST["persist finding identity + version 1&lt;br/&gt;origin = extraction"]
    PERSIST --> READY(["report → ready"])

    PX -.->|"PdfError / any exception"| FAIL(["report → failed&lt;br/&gt;error recorded"])
    EX -.-> FAIL

    style READY fill:#ebfbee,stroke:#2f9e44
    style FAIL fill:#fff5f5,stroke:#e03131
    style ENR fill:#fff9db,stroke:#f08c00
```

`run_extraction` guarantees the report always leaves `extracting` — to `ready`
with findings persisted, or to `failed` with the error recorded — so the SPA's
status poll is guaranteed to terminate. Document metadata extraction is
best-effort and can never fail a report.

For development and demos, seed through **manual entry**: it skips the LLM, so
seeding is deterministic, instant and free, and the result is indistinguishable
downstream from an extracted report.

## Reports chat — read-only corpus Q&A (FR-18, ADR-0036)

The chat is deliberately *not* context-stuffed with the corpus. It gets typed,
read-only query tools, so "how many findings mention SQL injection?" is answered
by a `COUNT`, not by an LLM estimating from a truncated prompt.

```mermaid
sequenceDiagram
    actor U as Auditor (browser)
    participant SPA as Chat.tsx
    participant API as FastAPI /api
    participant AG as reports_chat agent&lt;br/&gt;(Pydantic AI)
    participant DB as SQLite

    U->>SPA: "how many findings relate to SQLi?"
    SPA->>API: POST /api/chats/{id}/messages
    API->>DB: load thread → message_history
    API->>AG: run_sync(question, history)

    loop agent decides which tools it needs
        AG->>DB: corpus_overview() — counts by status / severity / latest verdict
        AG->>DB: find_findings(keyword, severity, report) — exact total, rows capped
        AG->>DB: list_reports()
        AG->>DB: get_finding(id) — full detail
    end

    AG-->>API: prose answer
    API->>DB: persist user + assistant turns
    API-->>SPA: whole updated thread
    SPA-->>U: answer

    Note over AG,DB: Every tool is read-only. No sandbox, no retest,&lt;br/&gt;no mutation is reachable from this agent.
```

Only prose turns are stored; the agent re-queries through its tools on every
turn, so an answer can never be stale relative to the database. Threads persist
(`chat_sessions` / `chat_messages`) so a conversation survives a reload.

!!! note "In flight: token-by-token streaming"
    Replies currently arrive in one block. An async SSE variant
    (`POST /api/chats/{id}/messages/stream`) is designed in **ADR-0038** and
    tracked by [#140](https://github.com/SelfishCoconut/revalid/issues/140);
    it is not on `main` yet.

## Model resolution — one switch, every component (FR-13, ADR-0010/0021)

There is exactly one model selection, and extraction, goal drafting, the retest
agent and the corpus chat all resolve through it. That is what makes the
local-versus-cloud comparison in the evaluation a fair one: identical code
paths, different backend.

```mermaid
flowchart TB
    ENV["environment&lt;br/&gt;REVALID_LLM_MODEL · OLLAMA_BASE_URL"] -->|"seeds ONCE, on a fresh DB"| ROW
    DEF["DEFAULT_MODEL&lt;br/&gt;ollama:qwen3.5:9b — local-first"] -->|"if env unset"| ROW
    ROW[("settings row&lt;br/&gt;authoritative, runtime-editable")]
    UI["Settings page&lt;br/&gt;PUT /api/settings"] -->|"overrides; env never wins again"| ROW

    ROW --> BM["llm.build_model(cfg)"]
    BM --> D1{"base_url set?"}
    D1 -->|yes| OAI["OpenAIChatModel via OpenAIProvider&lt;br/&gt;any OpenAI-compatible host, incl. Ollama"]
    D1 -->|no| D2{"anthropic + stored key?"}
    D2 -->|yes| ANT["AnthropicModel via AnthropicProvider"]
    D2 -->|no| STR["bare 'provider:model' string&lt;br/&gt;Pydantic AI resolves from env"]

    OAI --> USERS
    ANT --> USERS
    STR --> USERS
    USERS["every LLM-using component:&lt;br/&gt;extract · plan (goal) · retest_agent · reports_chat"]

    style ROW fill:#fff9db,stroke:#f08c00
    style USERS fill:#e7f5ff,stroke:#1971c2
```

`GET /api/settings/status` reports reachability and `POST /api/settings/probe`
discovers the models a provider actually offers, so the operator picks from a
live list rather than a hardcoded one.

In tests the model is never real: Pydantic AI's `TestModel`/`FunctionModel`
stand in, and `FakeSandbox` replaces Docker — the whole HTTP flow runs with no
network, no API key and no daemon.

## SPA route map (FR-11, FR-16, FR-17)

```mermaid
flowchart LR
    ROOT["/"] --> OV["ReportsOverview&lt;br/&gt;corpus + risk profile"]
    NEW["/new"] --> NR["NewReport&lt;br/&gt;upload / manual entry"]
    RD["/reports/:id"] --> RDV["ReportDetail&lt;br/&gt;findings of one report"]
    CH["/chat · /chat/:id"] --> CHV["Chat&lt;br/&gt;FR-18 corpus Q&A"]
    SET["/settings"] --> SETV["Settings&lt;br/&gt;FR-13 backend + display"]
    RS["/retest-sessions/:id"] --> RSV["RetestSessionRoute&lt;br/&gt;FR-17 console"]

    F["/findings/:id"] --> FL["FindingLayout&lt;br/&gt;stage wizard shell"]
    FL --> S1["/extract&lt;br/&gt;what was found"]
    FL --> S2["/goal&lt;br/&gt;what to verify"]
    FL --> S3["/retest&lt;br/&gt;the agentic session"]
    FL --> S4["/verdict&lt;br/&gt;the determination"]

    style FL fill:#fff9db,stroke:#f08c00
    style RSV fill:#e7f5ff,stroke:#1971c2
```

The stepper is **navigation only** — clicking a stage never mutates anything
(ADR-0024). Visiting `/findings/:id` bare redirects to the appropriate stage.

## Derivations off the trail

Both are read-only and touch no network.

```mermaid
flowchart LR
    T[("session_events&lt;br/&gt;append-only transcript")]
    V[("verdicts")]

    T --> AU["FR-10 audit&lt;br/&gt;GET /api/audit&lt;br/&gt;re-project authoritative event,&lt;br/&gt;diff against the stored row"]
    V --> AU
    AU --> R1["AuditReport&lt;br/&gt;{total, reproduced, discrepancies}"]

    V --> EXP["FR-12 export&lt;br/&gt;GET /api/export&lt;br/&gt;SCHEMA_VERSION 1.5"]
    EXP --> R2["RunExport document"]
    EXP --> SCH["GET /api/export/schema&lt;br/&gt;generated from the model —&lt;br/&gt;cannot drift from the document"]

    R2 --> EV["FR-15 evaluation&lt;br/&gt;make eval&lt;br/&gt;score against ground truth"]
    EV --> R3["correct / inconclusive / wrong&lt;br/&gt;NFR-01 gate"]

    style AU fill:#e7f5ff,stroke:#1971c2
    style EXP fill:#ebfbee,stroke:#2f9e44
    style EV fill:#fff9db,stroke:#f08c00
```

The evaluation harness is the only part that answers "is the verdict *right*" —
a question no other component in the pipeline can establish about itself.
