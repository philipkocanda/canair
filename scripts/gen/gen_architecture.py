#!/usr/bin/env python3
"""Generate the layer and package tables of docs/development/architecture.md.

The page's hand-written parts (the placement table, the rules) sit around two generated blocks:

* **Layers**, from the ``layers`` contract in ``[tool.importlinter]``, so the page cannot disagree
  with what ``lint-imports`` enforces.
* **Packages**, from each top-level package's charter (the ``Belongs:`` / ``Not here:`` /
  ``Imports:`` lines of its ``__init__.py`` docstring).

Usage:
    python3 scripts/gen/gen_architecture.py            # rewrite the blocks
    python3 scripts/gen/gen_architecture.py --check    # fail if the page is stale (CI)
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "src" / "canair"
PAGE = REPO_ROOT / "docs" / "development" / "architecture.md"
LAYERS_CONTRACT = "The layers hold"
CHARTER_KEYS = ("Belongs", "Not here", "Imports")


def _block(name: str) -> tuple[str, str]:
    return f"<!-- BEGIN GENERATED: {name} -->", f"<!-- END GENERATED: {name} -->"


def layers() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf8"))
    for contract in data["tool"]["importlinter"]["contracts"]:
        if contract["name"] == LAYERS_CONTRACT:
            return list(contract["layers"])
    raise SystemExit(f"no {LAYERS_CONTRACT!r} contract in pyproject.toml")


def _names(layer: str) -> list[str]:
    return [n.strip().removeprefix("canair.") for n in re.split(r"[|:]", layer)]


def render_layers() -> str:
    rows = ["| Layer | Packages | May import |", "|---|---|---|"]
    for i, layer in enumerate(layers(), start=1):
        names = _names(layer)
        if ":" in layer:
            kind = "each other (one cycle still to split) and the layers below"
        elif "|" in layer:
            kind = "the layers below, not each other"
        else:
            kind = "the layers below"
        rows.append(f"| {i} | {', '.join(f'`{n}/`' for n in names)} | {kind} |")
    return "\n".join(
        ["Top to bottom. A package imports only from the layers below its own.", "", *rows]
    )


def charter(package: Path) -> dict[str, str]:
    tree = ast.parse((package / "__init__.py").read_text(encoding="utf8"))
    doc = ast.get_docstring(tree) or ""
    out = {"summary": doc.strip().splitlines()[0] if doc.strip() else ""}
    for key in CHARTER_KEYS:
        match = re.search(
            rf"^{key}: (.*?)(?=\n(?:Belongs|Not here|Imports):|\n\n|\Z)", doc, re.S | re.M
        )
        out[key] = " ".join(match.group(1).split()) if match else ""
    return out


def render_packages() -> str:
    rows = ["| Package | What it is | Belongs | Not here | Imports |", "|---|---|---|---|---|"]
    for package in sorted(p for p in PACKAGE_DIR.iterdir() if (p / "__init__.py").is_file()):
        c = charter(package)
        cells = [f"`{package.name}/`", c["summary"], *(c[k] for k in CHARTER_KEYS)]
        rows.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(rows)


def render(page: str) -> str:
    for name, text in (("layers", render_layers()), ("packages", render_packages())):
        begin, end = _block(name)
        if begin not in page or end not in page:
            raise SystemExit(f"{PAGE.relative_to(REPO_ROOT)} has no {name} block ({begin})")
        head, rest = page.split(begin, 1)
        _, tail = rest.split(end, 1)
        page = f"{head}{begin}\n\n{text}\n\n{end}{tail}"
    return page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the page is out of date")
    args = parser.parse_args()
    current = PAGE.read_text(encoding="utf8")
    fresh = render(current)
    if args.check:
        if fresh != current:
            print(
                "architecture.md is out of date; run `python3 scripts/gen/gen_architecture.py`",
                file=sys.stderr,
            )
            return 1
        print("Architecture page is up to date.")
        return 0
    PAGE.write_text(fresh, encoding="utf8")
    print(f"wrote {PAGE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
