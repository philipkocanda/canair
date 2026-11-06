"""Hoist function-level imports of canair modules to the top of their module.

The text transform behind ``scripts/dev/hoist_deferred.py`` and the cycle trial of
``deferred_imports.py``. It moves exactly the statements it is told to move, merges them into the
module's import block, lets ruff sort that block, and changes nothing else. A statement it cannot
move without changing behaviour stays where it is, with the reason in ``Result.skipped``.
"""

from __future__ import annotations

import ast
import builtins
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deferred_scan as scan

PYPROJECT = scan.graph.REPO_ROOT / "pyproject.toml"
SCOPES = (*scan.FUNCTIONS, ast.ClassDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp)


@dataclass
class Result:
    text: str
    hoisted: list[scan.FunctionImport] = field(default_factory=list)
    dropped: list[scan.FunctionImport] = field(default_factory=list)  # already imported on top
    skipped: list[tuple[scan.FunctionImport, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def ruff(args: list[str], text: str, path: Path) -> str:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            *args,
            "--config",
            str(PYPROJECT),
            "--stdin-filename",
            str(path),
            "-",
        ],
        input=text,
        capture_output=True,
        text=True,
        cwd=scan.graph.REPO_ROOT,
        check=False,
    )
    if done.returncode not in (0,) or not done.stdout.strip():
        raise RuntimeError(f"ruff {' '.join(args[:1])} failed on {path}:\n{done.stderr}")
    return done.stdout


def tidy(text: str, path: Path) -> str:
    """Sort the import block the way ``ruff check`` wants it, then format."""
    sorted_ = ruff(["check", "--select", "I", "--fix-only", "--exit-zero", "--quiet"], text, path)
    return ruff(["format"], sorted_, path)


def module_bindings(
    tree: ast.Module, module: str, is_package: bool
) -> tuple[dict[str, set[tuple]], dict[str, tuple[int, str]]]:
    """Names bound at module level, each with the sources it comes from (``()`` = not an import).

    An import under ``TYPE_CHECKING`` has the source ``("tc",)``. A name that a function declares
    ``global`` counts as bound. The second result maps a module the file imports at the top to
    how the file spells it ``(level, name as written)``.
    """
    bound: dict[str, set[tuple]] = defaultdict(set)
    spelling: dict[str, tuple[int, str]] = {}

    def process(node: ast.AST, tc: bool) -> None:
        if isinstance(node, ast.ImportFrom):
            source = scan.graph._resolve(node, module, is_package)
            for a in node.names:
                key = ("tc",) if tc else ("from", source, a.name)
                bound[a.asname or a.name].add(key)
            if not tc:
                spelling.setdefault(source, (node.level, node.module or ""))
            return
        if isinstance(node, ast.Import):
            for a in node.names:
                bound[a.asname or a.name.split(".")[0]].add(("tc",) if tc else ("import",))
            return
        if isinstance(node, (*scan.FUNCTIONS, ast.ClassDef)):
            bound[node.name].add(())
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound[node.id].add(())
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound[node.name].add(())
        if isinstance(node, SCOPES):
            return
        if isinstance(node, ast.If) and scan._mentions_type_checking(node.test):
            for stmt in node.body:
                process(stmt, True)
            for stmt in node.orelse:
                process(stmt, tc)
            return
        for child in ast.iter_child_nodes(node):
            process(child, tc)

    for stmt in tree.body:
        process(stmt, False)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            for name in node.names:
                bound[name].add(())
    return bound, spelling


def _key(imp: scan.FunctionImport, name: str) -> tuple:
    return ("from", imp.source, name)


def _bound_name(name: str, asname: str | None) -> str:
    return asname or name


def hoist_text(
    module: str,
    path: Path,
    text: str,
    select: set[int],
    known: set[str],
    is_package: bool | None = None,
    format_result: bool = True,
) -> Result:
    """Hoist the function-level imports whose first line is in ``select``."""
    tree = ast.parse(text)
    lines = text.split("\n")
    found = scan_function_imports(module, path, text, known, is_package)
    nodes = {
        n.lineno: n
        for n, in_fn, _t, tc in scan.walk_imports(tree)
        if in_fn and not tc and isinstance(n, (ast.Import, ast.ImportFrom))
    }
    result = Result(text=text)
    top, spelling = module_bindings(
        tree, module, is_package if is_package is not None else path.name == "__init__.py"
    )
    chosen: list[scan.FunctionImport] = []
    for imp in found:
        if imp.line not in select:
            continue
        why = _refusal(imp, lines, nodes[imp.line])
        if why:
            result.skipped.append((imp, why))
        else:
            chosen.append(imp)

    # a name two chosen statements bind to different sources is not safe to hoist
    sources: dict[str, set[tuple]] = defaultdict(set)
    for imp in chosen:
        for name, asname in imp.names:
            sources[_bound_name(name, asname)].add(_key(imp, name))
    keep: list[scan.FunctionImport] = []
    for imp in chosen:
        why = ""
        for name, asname in imp.names:
            bound = _bound_name(name, asname)
            have = top.get(bound, set())
            if len(sources[bound]) > 1:
                why = f"{bound} is imported from two different places"
            elif have and _key(imp, name) not in have:
                why = (
                    f"{bound} is also imported under TYPE_CHECKING"
                    if ("tc",) in have
                    else f"{bound} is already bound at module level"
                )
        if why:
            result.skipped.append((imp, why))
        else:
            keep.append(imp)

    # a block that would be left empty keeps its last import
    by_block: dict[tuple[int, str], list[scan.FunctionImport]] = defaultdict(list)
    owner = _block_of(tree)
    for imp in keep:
        by_block[owner[imp.line]].append(imp)
    removable: list[scan.FunctionImport] = []
    for block, group in by_block.items():
        size = _block_size(tree, block)
        if len(group) >= size:
            for imp in group:
                result.skipped.append((imp, "it is the only statement of its block"))
        else:
            removable += group
    if not removable:
        return result

    new_lines = list(lines)
    spelled: dict[str, tuple[int, str]] = {}
    carried: dict[tuple[int, str], list[str]] = defaultdict(list)
    added: dict[tuple[int, str], set[str]] = defaultdict(set)
    for imp in removable:
        all_present = all(_key(imp, n) in top.get(_bound_name(n, a), set()) for n, a in imp.names)
        if all_present:
            result.dropped.append(imp)
        else:
            result.hoisted.append(imp)
            group = spelled.setdefault(
                imp.source, spelling.get(imp.source) or (imp.level, imp.rel_module)
            )
            for name, asname in imp.names:
                if _key(imp, name) not in top.get(_bound_name(name, asname), set()):
                    added[group].add(name + (f" as {asname}" if asname else ""))
            if imp.trailing_comment and imp.trailing_comment not in carried[group]:
                carried[group].append(imp.trailing_comment)

    for imp in sorted(removable, key=lambda i: i.line, reverse=True):
        start = imp.line
        while start >= 2 and new_lines[start - 2].lstrip().startswith(scan.REASON_MARK):
            start -= 1
        above = new_lines[start - 2].strip() if start >= 2 else ""
        if above.startswith("#"):
            result.notes.append(f"{path}:{start - 1}: comment above a hoisted import: {above}")
        del new_lines[start - 1 : imp.end_line]
        _close_gap(new_lines, start - 1)

    imports = []
    for (level, source), names in sorted(added.items()):
        comment = "  " + "  ".join(carried[(level, source)]) if carried[(level, source)] else ""
        imports.append(f"from {'.' * level}{source} import {', '.join(sorted(names))}{comment}")
    if imports:
        anchor = _import_anchor(tree)
        block = imports if anchor else ["", *imports]
        new_lines[anchor:anchor] = block
    out = "\n".join(new_lines)
    result.text = tidy(out, path) if format_result else out
    return result


def scan_function_imports(
    module: str, path: Path, text: str, known: set[str], is_package: bool | None
) -> list[scan.FunctionImport]:
    return scan.scan_text(module, path, text, known, is_package)[0]


def _refusal(imp: scan.FunctionImport, lines: list[str], node: ast.stmt) -> str:
    if imp.in_try:
        return "it sits in a try block"
    if not imp.is_from:
        return "plain `import` statement"
    if any(n == "*" for n, _ in imp.names):
        return "star import"
    if any(_bound_name(n, a) in dir(builtins) for n, a in imp.names):
        return "it would hide a builtin of the same name"
    first = lines[imp.line - 1]
    if first[: node.col_offset].strip():
        return "another statement shares its line"
    rest = lines[imp.end_line - 1][node.end_col_offset :].strip()  # type: ignore[index]
    if rest and not rest.startswith("#"):
        return "another statement shares its line"
    return ""


def _block_of(tree: ast.Module) -> dict[int, tuple[int, str]]:
    """Map the first line of each import statement to ``(id of the parent, field)``."""
    owner: dict[int, tuple[int, str]] = {}
    for parent in ast.walk(tree):
        for field_name in ("body", "orelse", "finalbody"):
            stmts = getattr(parent, field_name, None)
            if isinstance(stmts, list):
                for stmt in stmts:
                    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                        owner[stmt.lineno] = (id(parent), field_name)
    return owner


def _block_size(tree: ast.Module, block: tuple[int, str]) -> int:
    for parent in ast.walk(tree):
        if id(parent) == block[0]:
            return len(getattr(parent, block[1]))
    return 0


def _import_anchor(tree: ast.Module) -> int:
    """The line after which new module-level imports go (0 = top of the file)."""
    anchor = 0
    for i, stmt in enumerate(tree.body):
        if i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            anchor = stmt.end_lineno or 0
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            anchor = stmt.end_lineno or anchor
        else:
            break
    return anchor


def _close_gap(lines: list[str], at: int) -> None:
    """After deleting the lines that sat at ``at``: drop a blank line the gap would leave."""
    if at >= len(lines) or lines[at].strip():
        return
    prev = lines[at - 1].rstrip() if at else ""
    if not prev or prev.endswith(":"):
        del lines[at]
