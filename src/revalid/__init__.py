"""revalid — AI-driven revalidation of pentest findings.

Parses pentest reports, extracts findings and reproduction steps, then drives a
human-gated agentic retest against a contained target to verify applied fixes.
"""

from importlib.metadata import version

# Single source of truth: the version declared in pyproject.toml, read from the
# installed package metadata — so a release bump never drifts from the code
# (and the FR-12 export / NFR-02 lineage report the real version).
__version__ = version("revalid")


def health() -> str:
    """Return a static liveness string.

    Placeholder for the walking skeleton: proves the package imports, the
    test pyramid runs, and the docs pipeline picks up public symbols.
    """
    return "revalid ok"
