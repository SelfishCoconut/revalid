---
name: docs
description: Build, serve, or extend the project documentation site (MkDocs + mkdocstrings + auto-generated UML). Use for "build the docs", "add a docs page", "regenerate diagrams", or docs drift checks.
---

# Documentation site (docs-as-code)

Principle: anything derivable from code is **generated at build time** — never hand-edited, never stale.

## Build

- `make docs` → regenerates pyreverse UML (class + package diagrams, Mermaid format) into `docs/reference/generated/`, then `mkdocs build --strict`.
- `make docs-serve` → live preview.
- CI deploys to GitHub Pages on every push to `main` (`docs.yml`).

## What goes where

- **Generated (do not touch)**: `docs/reference/generated/` (pyreverse UML, pydeps graph), API pages via mkdocstrings `::: revalid.<module>` directives.
- **Authored**: `docs/architecture/` — C4 model (context/container/component) and sequence diagrams as Mermaid blocks in markdown. Diffable in PRs; GitHub renders them natively.
- **Authored**: how-to/usage pages under `docs/`.

## Rules

- New public symbol → Google-style docstring (it IS the documentation; mkdocstrings renders it).
- Code change that alters a flow described in an authored diagram → update the diagram in the same PR (the `doc-curator` agent checks this).
- `mkdocs build --strict` failing on a broken link/reference is a CI failure — fix, don't ignore.
- Architecture pages double as raw material for the thesis design chapter; keep them precise.
