# Architecture Decision Records

Decision log (MADR format, see the `adr` skill). A decision without an ADR doesn't exist.

| # | Title | Status | Date |
|---|-------|--------|------|
| [0001](0001-development-environment-and-process.md) | Development environment, AI governance and Kanban process | accepted | 2026-06-11 |
| [0002](0002-product-architecture.md) | Product architecture: FastAPI + React SPA, SQLite, Claude-primary LLM, plan-approve-execute + sanity checker | accepted | 2026-06-11 |
| [0003](0003-ci-gated-auto-merge.md) | CI-gated auto-merge replaces the manual pre-merge validation gate | accepted | 2026-07-01 |
| [0004](0004-right-size-solo-dev-process.md) | Right-size the solo-developer process: ceremony scales with thesis value | accepted | 2026-07-13 |
| [0005](0005-remove-ai-compliance-auditor.md) | Remove the ai-compliance-auditor agent; Álvaro owns §6 compliance directly | accepted | 2026-07-13 |
| [0006](0006-remove-enforced-data-policy.md) | Remove the enforced §6 data policy; Álvaro owns data handling directly | accepted | 2026-07-13 |
| [0007](0007-pdf-ingestion-pdfplumber.md) | PDF report ingestion: pdfplumber for extraction, a text seam to LLM structuring | proposed | 2026-07-13 |
| [0008](0008-single-user-threat-model.md) | Single trusted-user threat model: drop the security-auditor agent and PDF bomb-hardening | accepted | 2026-07-13 |
| [0009](0009-llm-extraction-architecture.md) | LLM finding extraction: per-candidate Pydantic AI with a schema-validation gate | proposed | 2026-07-13 |
