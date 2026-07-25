"""Render the thesis's architecture figures from the authored Mermaid sources.

The memoir's Design chapter depicts the same architecture as
``docs/architecture/*.md``. Copying those diagrams into TikZ by hand would create
two descriptions of one system, and the hand-made one would drift --- exactly the
failure mode the docs-as-code rule exists to prevent (see ``CLAUDE.md``). So the
thesis figures are *generated* from the documentation sources instead.

A block is opted in by a marker comment on the line above its fence::

    <!-- thesis-fig: containers -->
    ```mermaid
    flowchart TB
    ...
    ```

which renders to ``thesis/figs/arch-containers.pdf``. Unmarked blocks are ignored,
so the documentation can carry diagrams the memoir does not use.

Rendering needs ``@mermaid-js/mermaid-cli`` (fetched on demand with ``npx``) and a
Chromium binary. Neither is a project dependency: the output PDFs are committed,
so the LaTeX build --- locally and in ``thesis.yml`` --- needs no JavaScript
toolchain at all. Re-run ``make thesis-figs`` after editing a marked diagram.

Usage::

    uv run python scripts/gen_thesis_figs.py [--check] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCES = (
    Path("docs/architecture/c4.md"),
    Path("docs/architecture/data-model.md"),
    Path("docs/architecture/class-model.md"),
    Path("docs/architecture/workflow.md"),
    Path("docs/architecture/topology.md"),
    Path("docs/requirements/use-cases.md"),
)
OUT_DIR = Path("thesis/figs")
#: Rendered files carry this prefix so they are visibly generated, and so a
#: stray hand-added figure in thesis/figs/ is never mistaken for one of these.
PREFIX = "arch-"

MARKER = re.compile(r"^<!--\s*thesis-fig:\s*([a-z0-9-]+)\s*-->\s*$")
FENCE_OPEN = re.compile(r"^```mermaid\s*$")
FENCE_CLOSE = re.compile(r"^```\s*$")

#: Chromium locations to try, in order, before falling back to whatever Puppeteer
#: bundles. The Playwright cache is the one this project already populates.
CHROMIUM_CANDIDATES = (
    "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
    "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
)

#: Mermaid theme. Only typography is overridden: the diagrams' own ``classDef``
#: colours are meaningful (a red boundary is the sandbox network, a dashed edge
#: is a connection that does not exist), so they are left exactly as the
#: documentation draws them. Carlito is the template's font (never change it).
MERMAID_CONFIG = {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "Carlito, Calibri, sans-serif",
        "fontSize": "15px",
    },
    # Diagrams authored for a browser at full width come out two to three times
    # wider than an A4 text column, and scaling one down to fit renders its
    # labels illegible. Wrapping long labels trades width for height, which is
    # the dimension a page has to spare, and keeps the type readable once the
    # figure is scaled to \textwidth.
    "flowchart": {"useMaxWidth": False, "wrappingWidth": 165},
    "sequence": {"useMaxWidth": False, "width": 160, "wrap": True},
    "er": {"useMaxWidth": False},
    "class": {"useMaxWidth": False},
}


class RenderError(RuntimeError):
    """Raised when a diagram could not be rendered."""


def find_chromium() -> str | None:
    """Return a usable Chromium path, or ``None`` to let Puppeteer choose."""
    for pattern in CHROMIUM_CANDIDATES:
        expanded = Path(pattern).expanduser()
        matches = sorted(Path(expanded.anchor).glob(str(expanded.relative_to(expanded.anchor))))
        if matches:
            return str(matches[-1])
    return None


def extract(source: Path) -> dict[str, str]:
    """Return ``{figure name: mermaid source}`` for every marked block in ``source``.

    Args:
        source: A markdown file under ``docs/architecture/``.

    Returns:
        The marked diagrams, keyed by the name given in the marker comment.

    Raises:
        RenderError: If a marker is not followed by a mermaid fence, or if two
            blocks claim the same name.
    """
    lines = source.read_text(encoding="utf-8").splitlines()
    found: dict[str, str] = {}
    for index, line in enumerate(lines):
        marker = MARKER.match(line)
        if marker is None:
            continue
        name = marker.group(1)
        if index + 1 >= len(lines) or not FENCE_OPEN.match(lines[index + 1]):
            raise RenderError(
                f"{source}:{index + 1}: 'thesis-fig: {name}' is not followed by a ```mermaid fence"
            )
        body: list[str] = []
        for candidate in lines[index + 2 :]:
            if FENCE_CLOSE.match(candidate):
                break
            body.append(candidate)
        else:
            raise RenderError(f"{source}:{index + 1}: unterminated mermaid fence for '{name}'")
        if name in found:
            raise RenderError(f"duplicate thesis-fig name '{name}' (second in {source})")
        found[name] = "\n".join(body)
    return found


def collect() -> dict[str, str]:
    """Gather every marked diagram across all sources, rejecting duplicates."""
    everything: dict[str, str] = {}
    for source in SOURCES:
        if not source.is_file():
            raise RenderError(f"missing architecture source: {source}")
        for name, body in extract(source).items():
            if name in everything:
                raise RenderError(f"duplicate thesis-fig name '{name}' in {source}")
            everything[name] = body
    if not everything:
        raise RenderError("no '<!-- thesis-fig: ... -->' markers found in any source")
    return everything


def render(name: str, body: str, workdir: Path, chromium: str | None) -> Path:
    """Render one diagram to a tightly-cropped vector PDF and return its path.

    Args:
        name: The figure name from its marker comment.
        body: The mermaid source.
        workdir: A scratch directory for the intermediate input/config files.
        chromium: Browser executable for Puppeteer, or ``None`` for its default.

    Returns:
        The written PDF path under :data:`OUT_DIR`.

    Raises:
        RenderError: If ``mmdc`` fails or produces nothing.
    """
    src = workdir / f"{name}.mmd"
    src.write_text(body, encoding="utf-8")
    config = workdir / "mermaid.json"
    config.write_text(json.dumps(MERMAID_CONFIG), encoding="utf-8")
    puppeteer = workdir / "puppeteer.json"
    puppeteer.write_text(
        json.dumps(
            {"args": ["--no-sandbox"], **({"executablePath": chromium} if chromium else {})}
        ),
        encoding="utf-8",
    )
    out = OUT_DIR / f"{PREFIX}{name}.pdf"
    command = [
        "npx",
        "-y",
        "@mermaid-js/mermaid-cli@11",
        "-i",
        str(src),
        "-o",
        str(out),
        "-c",
        str(config),
        "-p",
        str(puppeteer),
        "--pdfFit",
        "--backgroundColor",
        "transparent",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0 or not out.is_file():
        raise RenderError(f"mmdc failed for '{name}':\n{result.stdout}\n{result.stderr}")
    return out


def select(diagrams: dict[str, str], only: list[str] | None) -> dict[str, str]:
    """Narrow ``diagrams`` to ``only``, or return it whole when no filter is given.

    Raises:
        RenderError: If a requested name is not a marked figure.
    """
    if not only:
        return diagrams
    unknown = sorted(set(only) - set(diagrams))
    if unknown:
        raise RenderError(f"no such figure(s): {', '.join(unknown)}")
    return {name: body for name, body in diagrams.items() if name in only}


def report_missing(diagrams: dict[str, str]) -> int:
    """List each figure's render state; non-zero when any output file is absent.

    This is the toolchain-free half of the script: it needs neither Node nor a
    browser, so a checkout can verify the committed PDFs cover every marked
    diagram without being able to regenerate them.
    """
    missing = [name for name in diagrams if not (OUT_DIR / f"{PREFIX}{name}.pdf").is_file()]
    for name in sorted(diagrams):
        print(f"  {'MISSING' if name in missing else 'ok':>7}  {PREFIX}{name}.pdf")
    if missing:
        print(f"\n{len(missing)} figure(s) not rendered — run `make thesis-figs`", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Render every marked diagram; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", metavar="NAME", help="render only these figures")
    parser.add_argument(
        "--check",
        action="store_true",
        help="list the figures that would be rendered and verify each has an output file",
    )
    args = parser.parse_args()

    try:
        diagrams = select(collect(), args.only)
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return report_missing(diagrams)

    if shutil.which("npx") is None:
        print("error: npx not found — Node.js is needed to render the diagrams", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chromium = find_chromium()
    if chromium:
        print(f"using chromium: {chromium}")
    os.environ.setdefault("PUPPETEER_SKIP_DOWNLOAD", "1" if chromium else "0")

    with tempfile.TemporaryDirectory(prefix="revalid-figs-") as tmp:
        workdir = Path(tmp)
        for name in sorted(diagrams):
            try:
                out = render(name, diagrams[name], workdir, chromium)
            except RenderError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    print(f"\n{len(diagrams)} figure(s) generated from {', '.join(str(s) for s in SOURCES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
