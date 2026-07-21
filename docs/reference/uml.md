# UML diagrams (generated)

Regenerated from the actual code by `pyreverse` on every docs build (`make docs`,
CI). Never edit by hand — they cannot be stale.

One diagram per architectural layer rather than one for the whole package. A
single dump of `revalid` is 96 classes joined by 18 relations, which is a wall of
disconnected boxes and says nothing about how the system fits together. The
groups below are the ones used by the [API reference](api.md), so the two pages
can be read side by side: this page gives the shape, that one gives the
docstrings.

Each layer pulls in one level of ancestors and associations from outside its own
modules, so an edge that crosses a boundary — `FindingExport` pointing at the
domain `Finding`, `AdjudicateRequest` at `VerdictStatus` — stays visible instead
of being cut at the group edge. That is why a class such as `Severity` appears in
more than one diagram: it is the same class, seen from each layer that depends on
it. Only classes revalid itself defines are drawn; library base types
(`pydantic.BaseModel` and friends) are pruned, since their boxes are larger than
anything in this codebase and describe the dependency rather than the system.

## Overview — package dependencies

The module-level view of the whole package: who imports whom. `app` is the
composition root and depends on nearly everything; `domain` sits at the bottom
and depends on nothing.

```mermaid
--8<-- "docs/reference/generated/packages_revalid.mmd"
```

## Core domain

`domain` holds the vocabulary the rest of the system agrees on — `Finding` and
its CVSS/MITRE enrichment, plus the enums that drive every state machine
(`Severity`, `VerdictStatus`, `FindingStage`, `RetestSessionStatus`). It imports
nothing from the other layers, which is the point.

```mermaid
--8<-- "docs/reference/generated/classes_domain.mmd"
```

## Report ingestion and understanding

The three doors into the corpus and the enrichment they share: `pdf` and
`extract` for the LLM-assisted PDF path, `ingest` for DefectDojo JSON and manual
entry, `findings` for versioning, notes and CVSS/MITRE enrichment, `llm` for the
model plumbing underneath.

```mermaid
--8<-- "docs/reference/generated/classes_ingestion.mmd"
```

## Retest goal and agentic session

`plan` produces the retest goal, `sandbox` is the egress-locked execution
environment, `retest_agent` is the Pydantic AI agent with its tools, and
`retest_session` is the orchestrator owning the lifecycle, transcript and
approval gate.

```mermaid
--8<-- "docs/reference/generated/classes_retest.mmd"
```

## Verdicts, audit and export

What comes out the far end: `audit` re-derives verdicts to check they still hold,
`export` serialises a run against the published JSON schema, and `eval` scores a
run against ground truth.

```mermaid
--8<-- "docs/reference/generated/classes_verdicts.mmd"
```

## Corpus chat, persistence and configuration

`reports_chat` is the corpus Q&A agent; `db` holds the SQLAlchemy row records
(every `*Record` descends from `Base`); `settings` is the runtime-editable LLM
backend configuration.

```mermaid
--8<-- "docs/reference/generated/classes_platform.mmd"
```

## HTTP API surface

Every Pydantic request and response model exposed by `app`, the FastAPI
composition root — the whole HTTP contract in one place, and by far the longest
diagram here. Field-by-field documentation lives in the
[API reference](api.md#application-and-configuration); the value of the diagram
is seeing which DTOs are projections of a domain type and which carry a domain
enum across the wire.

```mermaid
--8<-- "docs/reference/generated/classes_api.mmd"
```
