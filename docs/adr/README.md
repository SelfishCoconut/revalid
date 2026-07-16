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
| [0007](0007-pdf-ingestion-pdfplumber.md) | PDF report ingestion: pdfplumber for extraction, a text seam to LLM structuring | accepted | 2026-07-13 |
| [0008](0008-single-user-threat-model.md) | Single trusted-user threat model: drop the security-auditor agent and PDF bomb-hardening | accepted | 2026-07-13 |
| [0009](0009-llm-extraction-architecture.md) | LLM finding extraction: per-candidate Pydantic AI with a schema-validation gate | accepted | 2026-07-13 |
| [0010](0010-model-agnostic-llm-config.md) | Model-agnostic LLM config: `REVALID_LLM_MODEL` env var, Ollama via Pydantic AI | accepted | 2026-07-13 |
| [0011](0011-retest-plan-generation.md) | Retest-plan generation: LLM-proposed typed actions, deterministically gated | accepted | 2026-07-13 |
| [0012](0012-server-side-plan-approval-gate.md) | Server-side plan approval gate: versioned plan rows, single execution chokepoint | accepted | 2026-07-14 |
| [0013](0013-react-spa-architecture.md) | React SPA architecture: PDF-ingest background jobs, `/api` prefix, FastAPI-served SPA | accepted | 2026-07-14 |
| [0014](0014-execution-sanity-checker.md) | Execution sanity checker: independent verifier — fail-closed plan-deviation block + conservative ambiguity downgrade | accepted | 2026-07-15 |
| [0015](0015-audit-trail-verdict-rederivation.md) | Audit trail: verdicts re-derivable from stored evidence via a shared pure assessment | accepted | 2026-07-15 |
| [0016](0016-versioned-run-export.md) | Versioned run export: Pydantic-generated JSON document + published, drift-tested JSON schema | accepted | 2026-07-15 |
| [0017](0017-evaluation-harness-nfr01-scoring.md) | Evaluation harness: score an FR-12 export against title-keyed ground truth with conservative NFR-01 buckets | proposed | 2026-07-15 |
| [0018](0018-browser-driven-probes-playwright.md) | Browser-driven probes via Playwright (optional extra), as a swapped executor under the unchanged FR-08 guard | proposed | 2026-07-15 |
| [0019](0019-retest-technique-registry.md) | Extensible retest-technique registry: kind-keyed assessors + command rendering, FR-04 kind tagging (scope stays human-validated) | proposed | 2026-07-15 |
| [0020](0020-manual-report-entry.md) | Manual report entry: human ingestion (form + JSON upload) bypassing the LLM | proposed | 2026-07-15 |
| [0021](0021-user-configurable-model-provider-setting.md) | User-configurable model/provider setting: DB-persisted, runtime-switchable, env-seeded | proposed | 2026-07-15 |
| [0022](0022-async-plan-generation.md) | Asynchronous plan generation: a persisted `generating` version settled by a background job | proposed | 2026-07-16 |
| [0023](0023-plan-iteration-instructions-regenerate-revise.md) | Plan iteration: operator instructions + regenerate / revise (go back a step) | proposed | 2026-07-16 |
| [0024](0024-finding-revision-annotation-stage-wizard.md) | Finding revision & annotation; pipeline stage wizard (supersedes ADR-0023's confirm-on-click) | accepted | 2026-07-16 |
| [0025](0025-agentic-retest-console.md) | Agentic retest console (Slice 0): egress-locked sandbox, deferred-tool gating, transcript audit (supersedes FR-04/05/07-09 over time) | proposed | 2026-07-16 |
| [0026](0026-operator-manual-commands-no-shared-pty.md) | Operator manual commands (`!`) via discrete exec — not a shared PTY; agent observes them on its next turn | proposed | 2026-07-16 |
