#!/usr/bin/env python3
"""Check that the code paths named in the agent documents still exist.

``AGENTS.md``, the skills and the developer docs name modules, symbols and test files. A move
leaves those names behind, and an agent that follows a stale one wastes a search or edits the
wrong file. This scan reads the documents and reports three kinds of stale reference:

* **missing file** — ``src/canair/…/x.py`` is not a file.
* **missing symbol** — ``src/canair/…/x.py::name`` names something that module does not define
  at its top level (or, for ``Class.attr``, in the class body). Imports, assignments, functions
  and classes count, also inside a module-level ``if`` or ``try``.
* **missing test** — a ``tests/…`` path is not a file or directory.

A path with a wildcard, a placeholder (``<cmd>``, ``{a,b}``) or an ellipsis is not a path and is
skipped. ``plans/`` and ``CHANGELOG.md`` are not scanned: they are historical records.

Usage:
    uv run python scripts/check/doc_paths.py             # exit 1 when anything is stale
    uv run python scripts/check/doc_paths.py FILE ...    # check these documents only
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DOC_GLOBS = ("AGENTS.md", ".claude/skills/*/SKILL.md", "docs/development/*.md")

_BEFORE = r"(?<![\w/.\-])"
_SRC_REF = re.compile(
    _BEFORE + r"(src/canair/[\w/.\-]*[\w/]\.py)(?![\w/\-*<{\[]|\.\w)"
    r"(?:::([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?)?"
)
_TEST_REF = re.compile(_BEFORE + r"(tests/[\w/.\-]*[\w/])(?![\w*<{\[/\-]|\.\w)")


@dataclass(frozen=True)
class Finding:
    doc: str
    line: int
    kind: str
    reference: str

    def __str__(self) -> str:
        return f"{self.doc}:{self.line}: {self.kind}: {self.reference}"


def documents(root: Path) -> list[Path]:
    """The documents under ``root`` that name code."""
    found: list[Path] = []
    for pattern in DOC_GLOBS:
        found.extend(sorted(root.glob(pattern)))
    return found


def _bound_names(body: Iterable[ast.stmt]) -> set[str]:
    """Names a block binds, looking into ``if``/``try``/``with`` but not into functions."""
    names: set[str] = set()
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.If | ast.With | ast.AsyncWith | ast.For | ast.While):
            names |= _bound_names(node.body) | _bound_names(getattr(node, "orelse", []))
        elif isinstance(node, ast.Try):
            blocks = [node.body, node.orelse, node.finalbody, *(h.body for h in node.handlers)]
            for block in blocks:
                names |= _bound_names(block)
    return names


class _Modules:
    """Parses each referenced module once."""

    def __init__(self) -> None:
        self._tops: dict[Path, tuple[set[str], dict[str, set[str]]]] = {}

    def lookup(self, path: Path) -> tuple[set[str], dict[str, set[str]]]:
        if path not in self._tops:
            tree = ast.parse(path.read_text(encoding="utf8"))
            classes = {
                node.name: _bound_names(node.body)
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
            }
            self._tops[path] = (_bound_names(tree.body), classes)
        return self._tops[path]


def scan(root: Path, docs: Sequence[Path] | None = None) -> list[Finding]:
    """Every stale reference in ``docs`` (default: the agent documents under ``root``)."""
    modules = _Modules()
    findings: list[Finding] = []
    for doc in documents(root) if docs is None else docs:
        name = doc.relative_to(root).as_posix() if doc.is_relative_to(root) else str(doc)
        for number, text in enumerate(doc.read_text(encoding="utf8").splitlines(), 1):
            for match in _SRC_REF.finditer(text):
                findings.extend(_check_source(root, modules, name, number, match))
            for match in _TEST_REF.finditer(text):
                if not (root / match.group(1)).exists():
                    findings.append(Finding(name, number, "missing test", match.group(1)))
    return findings


def _check_source(
    root: Path, modules: _Modules, doc: str, line: int, match: re.Match[str]
) -> list[Finding]:
    path, symbol, member = match.groups()
    file = root / path
    if not file.is_file():
        return [Finding(doc, line, "missing file", path)]
    if symbol is None:
        return []
    top, classes = modules.lookup(file)
    if symbol not in top:
        return [Finding(doc, line, "missing symbol", f"{path}::{symbol}")]
    if member is not None and symbol in classes and member not in classes[symbol]:
        return [Finding(doc, line, "missing symbol", f"{path}::{symbol}.{member}")]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="*", type=Path, help="documents to check (default: all)")
    args = parser.parse_args(argv)
    docs = [f.resolve() for f in args.files] or None
    findings = scan(REPO_ROOT, docs)
    for finding in findings:
        print(finding)
    if findings:
        print(f"\n{len(findings)} stale path reference(s). Fix the text, not the check.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
