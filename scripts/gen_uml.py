"""Generate the UML diagrams published at ``docs/reference/uml.md``.

Usage::

    make uml          # local, and what the Pages workflow runs

Emits one mermaid class diagram *per architectural layer* plus the whole-package
dependency diagram, into ``docs/reference/generated/`` (gitignored — always
rebuilt from the code, so it cannot go stale).

Why not one ``pyreverse`` run over the whole package: that produces 96 classes
joined by 18 relations, which mermaid lays out as a 30 000 px-wide row of
disconnected boxes — unreadable, and it says nothing about how the system fits
together (issue #158). The ``LAYERS`` groups below mirror the sections of
``docs/reference/api.md`` so the two pages can be read side by side.

Each layer is generated with ``-s 1 -a 1`` so that a class it depends on but does
not define is still drawn, keeping cross-layer edges (``FindingOut --|> Finding``)
instead of cutting them at the group boundary. Those flags also drag in the
*library* base types — ``pydantic.BaseModel``, ``ConfigDict``, ``TypedDict`` —
whose boxes are far larger than anything in this codebase, so :func:`prune`
drops every class revalid does not itself define. The roster of what revalid
defines comes from the whole-package run rather than a hardcoded denylist, so
swapping a dependency needs no change here.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

OUT_DIR = Path("docs/reference/generated")
SRC = Path("src/revalid")

#: Diagram name -> the modules it covers. Mirrors the sections of docs/reference/api.md.
LAYERS: dict[str, tuple[str, ...]] = {
    "domain": ("domain",),
    "ingestion": ("ingest", "pdf", "llm", "extract", "findings"),
    "retest": ("plan", "sandbox", "retest_agent", "retest_session"),
    "verdicts": ("audit", "export", "eval"),
    "platform": ("reports_chat", "settings", "db"),
    "api": ("app",),
}

CLASS_OPEN = re.compile(r"^  class (\w+)\s*\{?$")
EDGE = re.compile(r"^  (\w+) (--\|>|--\*|-->|o--|\.\.>) (\w+)")
#: Pydantic stamps ``model_config`` on every model; it is boilerplate in all 96 boxes.
BOILERPLATE_ATTR = re.compile(r"^    model_config\b")


def pyreverse(project: str, targets: list[str], *, related: bool) -> None:
    """Run pyreverse for one diagram, writing ``classes_<project>.mmd`` to `OUT_DIR`.

    Args:
        project: pyreverse project name; becomes the output filename suffix.
        targets: Paths to the modules or package to diagram.
        related: Pull in one level of ancestors and associated classes defined
            outside `targets`, so cross-layer edges survive the split.
    """
    cmd = ["uv", "run", "pyreverse", "-o", "mmd", "-d", str(OUT_DIR), "-p", project]
    if related:
        cmd += ["-s", "1", "-a", "1"]
    subprocess.run([*cmd, *targets], check=True)  # noqa: S603 - fixed argv, no shell


def defined_classes(dump: Path) -> set[str]:
    """Return the classes revalid itself defines, per a whole-package pyreverse dump."""
    return {m.group(1) for line in dump.read_text().splitlines() if (m := CLASS_OPEN.match(line))}


def prune(diagram: Path, keep: set[str]) -> None:
    """Rewrite `diagram` in place with only the classes in `keep`.

    Drops foreign classes (library base types pulled in by ``-a``/``-s``) together
    with any edge touching them, strips Pydantic's ``model_config`` boilerplate
    attribute, and sets a left-to-right rank direction.

    Mermaid's default top-down direction lays *unconnected* boxes out in a single
    row, so a diagram of mostly-independent DTOs grows sideways without bound —
    the HTTP layer measured 11 154 px wide. Left-to-right turns that growth
    vertical (2 844 px wide), and vertical is the axis a reader already scrolls.

    Args:
        diagram: Path to a mermaid class diagram, overwritten in place.
        keep: Names of the classes to retain.
    """
    out: list[str] = []
    skipping = False
    for line in diagram.read_text().splitlines():
        if skipping:
            skipping = line != "  }"
            continue
        if opened := CLASS_OPEN.match(line):
            if opened.group(1) not in keep:
                # A bodyless `class Foo` line has nothing to skip past.
                skipping = line.endswith("{")
                continue
        elif edge := EDGE.match(line):
            if not {edge.group(1), edge.group(3)} <= keep:
                continue
        elif BOILERPLATE_ATTR.match(line):
            continue
        out.append(line)
        if line == "classDiagram":
            out.append("  direction LR")
    diagram.write_text("\n".join(out) + "\n")


def main() -> int:
    """Regenerate every published UML diagram from the current source tree."""
    if not SRC.is_dir():
        print(f"run from the repository root: {SRC} not found", file=sys.stderr)
        return 1

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    OUT_DIR.mkdir(parents=True)

    # The whole-package run gives the package dependency diagram we publish and the
    # roster of classes revalid defines; its 96-class dump is what this script replaces.
    pyreverse("revalid", [str(SRC)], related=False)
    roster = defined_classes(OUT_DIR / "classes_revalid.mmd")
    (OUT_DIR / "classes_revalid.mmd").unlink()

    for name, modules in LAYERS.items():
        pyreverse(name, [str(SRC / f"{m}.py") for m in modules], related=True)
        (OUT_DIR / f"packages_{name}.mmd").unlink(missing_ok=True)
        prune(OUT_DIR / f"classes_{name}.mmd", roster)

    print(f"{len(LAYERS)} layer diagrams + package overview -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
