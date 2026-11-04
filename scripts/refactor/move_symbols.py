#!/usr/bin/env python3
"""Rewrite importers when code moves between modules (the codemod behind the layout plan).

Three operations, each applied to every ``*.py`` under ``src/canair/``, ``tests/`` and ``scripts/``:

    move-names OLD_MODULE NEW_MODULE NAME [NAME ...]
        Names moved from one module to another: ``from OLD import a, b`` becomes
        ``from NEW import a, b`` (aliases, indentation and function-level position are kept;
        a statement that also imports other names is split). Dotted strings such as
        ``"OLD.a"`` in ``monkeypatch.setattr`` calls are rewritten too. It does NOT move the
        code: cut and paste the definition first, then run this.

    rename-module OLD_MODULE NEW_MODULE
        A module (or package) renamed or moved: absolute and relative imports of it and of its
        submodules, ``from PARENT import leaf``, ``import OLD.x`` and dotted strings. Run
        ``git mv`` first. Files inside OLD or NEW are skipped, since they moved together and
        their relative imports still resolve.

    retarget-paths OLD_PATH NEW_PATH [OLD_PATH NEW_PATH ...]
        ``src/canair/x/y.py`` style paths in docs, skills, comments and config (tracked text files
        outside ``plans/``, ``profiles/``, ``tests/fixtures/`` and ``CHANGELOG.md``).

What it does not handle, learnt the hard way:

* ``from . import mod`` when ``mod`` moves: the rewrite drops the ``mod`` alias if the old name
  was used unqualified, and ``ruff --fix`` then deletes the "unused" import. Check for F821.
* ``import pkg.mod as alias`` style module attribute access (``alias.func``) in tests: the
  importers are rewritten but ``alias.func`` still points at the old module. ``ty``/the suite
  will find them; fix by hand.
* Names resolved through a module-level ``__getattr__`` (``canair.render.ansi.BOLD``) must never be
  patched with ``monkeypatch.setattr``; use ``monkeypatch.setitem(module.__dict__, ...)``.
* Imports it writes are absolute and may land in their own isort group; run
  ``ruff check --fix`` and ``ruff format`` afterwards, and mind the size ceilings in
  ``tests/contracts/baseline.json`` (an extra import line can push a module over its ceiling).
* ``[tool.importlinter]`` module names in ``pyproject.toml`` and the keys of
  ``tests/contracts/baseline.json`` are not Python imports: update them by hand.

Always run from the repository root.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOTS = ("src/canair", "tests", "scripts")
TEXT_SUFFIXES = (".py", ".md", ".yml", ".yaml", ".toml", ".sh", ".tape")
PATH_SKIP = ("plans/", "profiles/", "tests/fixtures/")


def module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    """The absolute module an ``ImportFrom`` names, resolving relative levels."""
    if not node.level:
        return node.module or ""
    base = module.split(".") if is_package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def render(module: str, names: list[ast.alias], indent: str, width: int = 100) -> str:
    items = [f"{a.name} as {a.asname}" if a.asname else a.name for a in names]
    one = f"{indent}from {module} import {', '.join(items)}"
    if len(one) <= width:
        return one
    body = "".join(f"{indent}    {item},\n" for item in items)
    return f"{indent}from {module} import (\n{body}{indent})"


def _python_files(roots: tuple[str, ...]):
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _apply(lines: list[str], edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(set(edits), reverse=True):
        lines[start - 1 : end] = replacement.split("\n")
    return "\n".join(lines)


def move_names(old: str, new: str, names: set[str], roots: tuple[str, ...] = ROOTS) -> list[str]:
    touched = []
    for path in _python_files(roots):
        text = path.read_text(encoding="utf8")
        if not any(n in text for n in names):
            continue
        module = module_name(path)
        if module == new:
            continue
        lines = text.split("\n")
        edits = []
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.ImportFrom):
                continue
            if resolve(node, module, path.name == "__init__.py") != old:
                continue
            moving = [a for a in node.names if a.name in names]
            if not moving:
                continue
            staying = [a for a in node.names if a.name not in names]
            first = lines[node.lineno - 1]
            indent = first[: len(first) - len(first.lstrip())]
            out = []
            if staying:
                out.append(render("." * node.level + (node.module or ""), staying, indent))
            out.append(render(new, moving, indent))
            edits.append((node.lineno, node.end_lineno or node.lineno, "\n".join(out)))
        result = _apply(lines, edits) if edits else text
        for name in names:
            pattern = rf"(?<![\w.]){re.escape(old)}\.{re.escape(name)}\b"
            result = re.sub(pattern, f"{new}.{name}", result)
        if result != text:
            path.write_text(result, encoding="utf8")
            touched.append(str(path))
    return touched


def rename_module(old: str, new: str, roots: tuple[str, ...] = ROOTS) -> list[str]:
    old_parent, _, old_leaf = old.rpartition(".")
    new_parent, _, new_leaf = new.rpartition(".")
    touched = []
    for path in _python_files(roots):
        text = path.read_text(encoding="utf8")
        if old_leaf not in text:
            continue
        module = module_name(path)
        inside = (old, new)
        if any(module == m or module.startswith(m + ".") for m in inside):
            continue
        lines = text.split("\n")
        edits = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                target = resolve(node, module, path.name == "__init__.py")
                first = lines[node.lineno - 1]
                indent = first[: len(first) - len(first.lstrip())]
                end = node.end_lineno or node.lineno
                if target == old or target.startswith(old + "."):
                    edits.append(
                        (node.lineno, end, render(new + target[len(old) :], node.names, indent))
                    )
                elif target == old_parent and any(a.name == old_leaf for a in node.names):
                    moving = [a for a in node.names if a.name == old_leaf]
                    staying = [a for a in node.names if a.name != old_leaf]
                    out = []
                    if staying:
                        out.append(render("." * node.level + (node.module or ""), staying, indent))
                    renamed = ast.alias(name=new_leaf, asname=moving[0].asname)
                    out.append(render(new_parent, [renamed], indent))
                    edits.append((node.lineno, end, "\n".join(out)))
            elif isinstance(node, ast.Import):
                if any(a.name == old or a.name.startswith(old + ".") for a in node.names):
                    segment = "\n".join(lines[node.lineno - 1 : node.end_lineno]).replace(old, new)
                    edits.append((node.lineno, node.end_lineno or node.lineno, segment))
        result = _apply(lines, edits) if edits else text
        result = re.sub(rf"(?<![\w.]){re.escape(old)}(?!\w)", new, result)
        if result != text:
            path.write_text(result, encoding="utf8")
            touched.append(str(path))
    return touched


def retarget_paths(pairs: dict[str, str]) -> list[str]:
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    touched = []
    for name in tracked.stdout.split("\n"):
        if not name or name.startswith(PATH_SKIP) or name == "CHANGELOG.md":
            continue
        path = Path(name)
        if not name.endswith(TEXT_SUFFIXES) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf8")
        except UnicodeDecodeError:
            continue
        result = text
        for old, new in pairs.items():
            result = re.sub(rf"(?<![\w/]){re.escape(old)}(?!\w)", new, result)
        if result != text:
            path.write_text(result, encoding="utf8")
            touched.append(name)
    return touched


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in {"move-names", "rename-module", "retarget-paths"}:
        print(__doc__)
        return 2
    command, args = argv[0], argv[1:]
    if command == "move-names" and len(args) >= 3:
        touched = move_names(args[0], args[1], set(args[2:]))
    elif command == "rename-module" and len(args) == 2:
        touched = rename_module(args[0], args[1])
    elif command == "retarget-paths" and len(args) >= 2 and len(args) % 2 == 0:
        touched = retarget_paths(dict(zip(args[::2], args[1::2], strict=True)))
    else:
        print(__doc__)
        return 2
    for name in touched:
        print(name)
    print(f"{len(touched)} file(s) rewritten. Now: ruff check --fix, ruff format, ty, tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
