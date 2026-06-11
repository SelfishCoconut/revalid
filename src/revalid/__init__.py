"""revalid — AI-driven revalidation of pentest findings.

Parses pentest reports, extracts findings and reproduction steps, and
re-executes them against authorized lab targets to verify applied fixes.
"""

__version__ = "0.1.0"


def health() -> str:
    """Return a static liveness string.

    Placeholder for the walking skeleton: proves the package imports, the
    test pyramid runs, and the docs pipeline picks up public symbols.
    """
    return "revalid ok"
