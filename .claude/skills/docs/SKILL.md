---
name: docs
description: Build, serve, or extend the project documentation site (MkDocs + mkdocstrings + auto-generated UML). Use for "build the docs", "add a docs page", "regenerate diagrams", or docs drift checks.
---

# Documentation site (docs-as-code)

Principle: anything derivable from code is **generated at build time** — never hand-edited, never stale.

## Build

- `make uml` → regenerates the pyreverse diagrams (Mermaid) into `docs/reference/generated/` via `scripts/gen_uml.py`: the package dependency graph plus one class diagram per group of modules. **Adding a module to `src/revalid/` means adding it to `LAYERS` in that script** — the build fails and names the module if you don't, because a module no group claims would silently vanish from the page.
- `make docs` → `make uml`, then `mkdocs build --strict`. The Pages workflow runs the same two steps, so local and CI cannot drift.
- `make docs-serve` → live preview.
- CI deploys to GitHub Pages on every push to `main` (`docs.yml`).

## What goes where

- **Generated (do not touch)**: `docs/reference/generated/` (pyreverse UML), API pages via mkdocstrings `::: revalid.<module>` directives.
- **Authored**: `docs/architecture/` — C4 model (context/container/component) and sequence diagrams as Mermaid blocks in markdown. Diffable in PRs; GitHub renders them natively.
- **Authored**: how-to/usage pages under `docs/`.

## Rules

- New public symbol → Google-style docstring (it IS the documentation; mkdocstrings renders it).
- Code change that alters a flow described in an authored diagram → update the diagram in the same PR (the `doc-curator` agent checks this).
- `mkdocs build --strict` failing on a broken link/reference is a CI failure — fix, don't ignore.
- Architecture pages double as raw material for the thesis design chapter; keep them precise.
