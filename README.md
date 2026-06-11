# revalid

**AI-Driven System for the Revalidation of Pentest Findings** — Bachelor's Thesis (TFG), Grado en Ingeniería Informática, Escuela Superior de Ingeniería Informática (ESII), Universidad de Castilla-La Mancha.

`revalid` parses penetration-testing reports, extracts the findings and their reproduction steps, and autonomously re-executes those steps against the audited systems to determine whether the applied fixes are effective — cutting the manual effort of post-audit revalidation.

> **Status**: environment setup / requirements phase. The walking skeleton is the first milestone.

## Project layout

| Path | Purpose |
|---|---|
| `src/revalid/` | The tool itself (Python 3.12+, managed with [uv](https://docs.astral.sh/uv/)) |
| `tests/` | `unit/`, `integration/`, `system/` test levels + `data/` synthetic sample reports |
| `thesis/` | The thesis memoir (English, ESII XeLaTeX template) |
| `docs/` | Project documentation: requirements (SRS), ADRs, AI-usage log, MkDocs sources |
| `scripts/demo/` | Runnable validation demos referenced by PRs ("How to validate") |

## Development

```sh
uv sync                 # install environment
make lint typecheck     # ruff + mypy
make test               # all test levels (unit / integration / system)
make docs               # build documentation site (auto-generated UML included)
make thesis             # build the thesis PDF (XeLaTeX)
```

## Use of AI — transparency notice

This project is developed with the assistance of **Claude Code** (Anthropic), in compliance with the ESII TFG regulation (Reglamento de Trabajos Fin de Grado, ESII, Feb 2026, Section 6):

- Every AI-assisted work session is recorded in [`docs/ai-usage/`](docs/ai-usage/); AI-assisted commits carry a `Co-Authored-By` trailer.
- All design decisions are made and all AI output is reviewed and validated by the author; decisions are recorded as ADRs in [`docs/adr/`](docs/adr/).
- No personal data or protected third-party information is ever provided to AI tools; all pentest data in this repository is synthetic or comes from intentionally vulnerable lab targets.
- The thesis includes the mandatory declaration of AI tools used, type of use, and affected sections.

## Safety & ethics

`revalid` only ever executes retests against **explicitly authorized targets** (by default, local lab containers such as OWASP Juice Shop / DVWA). It is a *revalidation* tool for findings already reported in an authorized audit — not an attack tool.

## License

[Beerware (Revision 42)](LICENSE) — not OSI-certified, intentionally so. 🍺
