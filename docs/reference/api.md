# API reference

Generated from docstrings by mkdocstrings — edit the code, not this page. For
the narrative of how these modules fit together, read
[How it works](../architecture/workflow.md).

::: revalid

::: revalid.domain

## Report ingestion and understanding

`pdf` extracts text and finding candidates; `extract` turns those into
schema-validated findings with an LLM; `ingest` maps DefectDojo-style JSON and
manual entry with no LLM at all; `findings` owns versioning, notes and the
CVSS/MITRE enrichment every door shares.

::: revalid.ingest

::: revalid.pdf

::: revalid.llm

::: revalid.extract

::: revalid.findings

## Retest goal and agentic session

`plan` generates the retest **goal** (FR-04, repurposed by ADR-0032);
`sandbox` provides the egress-locked execution environment; `retest_agent` is the
Pydantic AI agent and its two tools; `retest_session` is the orchestrator that
owns the lifecycle, the transcript and the approval gate.

::: revalid.plan

::: revalid.sandbox

::: revalid.retest_agent

::: revalid.retest_session

## Verdicts, audit and export

::: revalid.audit

::: revalid.export

::: revalid.eval

## Corpus chat

::: revalid.reports_chat

## Application and configuration

::: revalid.settings

::: revalid.db

::: revalid.app
