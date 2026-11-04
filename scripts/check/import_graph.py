#!/usr/bin/env python3
"""Measure the shape of the canair package: import cycles, deferred imports, root modules, size.

The numbers in plans/2026-11-04-package-layout-and-layering.md come from this scan, and
tests/contracts/test_structure.py reads the same functions so the two cannot disagree.

Usage:
    uv run python scripts/check/import_graph.py             # human report
    uv run python scripts/check/import_graph.py --json      # the same numbers, machine-readable
    uv run python scripts/check/import_graph.py --edges A:B # module edges from unit A to unit B

A *unit* is a top-level subpackage or root module of the package (``canlib.uds`` -> ``uds``).
Edges from or to the package root itself are left out of the cycle count, because the root
``__init__`` re-exports from many units and would join them all.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "canlib"
OVERSIZED_LINES = 500


@dataclass
class Scan:
    modules: dict[str, Path] = field(default_factory=dict)
    # module -> modules it imports (internal only)
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # module -> count of internal imports that sit inside a function or an `if`
    deferred: dict[str, int] = field(default_factory=dict)
    argparse_importers: set[str] = field(default_factory=set)
    textual_importers: set[str] = field(default_factory=set)
    lines: dict[str, int] = field(default_factory=dict)


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if not node.level:
        return node.module or ""
    base = module.split(".") if is_package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def scan(root: Path | None = None) -> Scan:
    """Parse every module under the package and collect its imports."""
    root = root or REPO_ROOT / PACKAGE
    out = Scan()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = module_name(path, root)
        is_package = path.name == "__init__.py"
        text = path.read_text(encoding="utf8")
        out.modules[name] = path
        out.lines[name] = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        tree = ast.parse(text)
        top = {id(n) for n in tree.body}
        deferred = 0
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [_resolve(node, name, is_package)]
            else:
                continue
            for target in targets:
                head = target.split(".")[0]
                if head == "argparse":
                    out.argparse_importers.add(name)
                elif head == "textual":
                    out.textual_importers.add(name)
                elif head == PACKAGE:
                    out.edges[name].add(target)
                    if id(node) not in top:
                        deferred += 1
        out.deferred[name] = deferred
    return out


def unit(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else PACKAGE


def unit_edges(result: Scan) -> dict[tuple[str, str], list[str]]:
    agg: dict[tuple[str, str], list[str]] = defaultdict(list)
    for src, targets in result.edges.items():
        for tgt in targets:
            a, b = unit(src), unit(tgt)
            if a != b:
                agg[(a, b)].append(f"{src} -> {tgt}")
    return agg


def cycles(result: Scan) -> list[list[str]]:
    """Strongly connected components (size > 1) of the unit graph, root left out."""
    graph: dict[str, set[str]] = defaultdict(set)
    for a, b in unit_edges(result):
        if PACKAGE not in (a, b):
            graph[a].add(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = [0]
    sys.setrecursionlimit(10_000)

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                found.append(sorted(comp))

    for v in list(graph):
        if v not in index:
            visit(v)
    return sorted(found, key=len, reverse=True)


def root_modules(result: Scan) -> list[str]:
    """Modules that sit directly in the package root (not ``__init__``/``__main__``)."""
    root = REPO_ROOT / PACKAGE
    return sorted(
        path.stem
        for path in result.modules.values()
        if path.parent == root and path.name not in {"__init__.py", "__main__.py"}
    )


def oversized(result: Scan, limit: int = OVERSIZED_LINES) -> dict[str, int]:
    return {m: n for m, n in sorted(result.lines.items()) if n > limit}


def summary(result: Scan) -> dict:
    comps = cycles(result)
    return {
        "modules": len(result.modules),
        "lines": sum(result.lines.values()),
        "root_modules": len(root_modules(result)),
        "largest_cycle_units": len(comps[0]) if comps else 0,
        "deferred_internal_imports": sum(result.deferred.values()),
        "deferred_in_files": sum(1 for n in result.deferred.values() if n),
        "argparse_importers_outside_cli": len(
            [m for m in result.argparse_importers if not m.startswith(f"{PACKAGE}.commands")]
        ),
        "textual_importers_outside_tui": len(
            [m for m in result.textual_importers if not m.startswith(f"{PACKAGE}.tui")]
        ),
        "modules_over_limit": len(oversized(result)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--edges", metavar="A:B", help="list module edges from unit A to unit B")
    args = parser.parse_args(argv)
    result = scan()
    if args.edges:
        a, b = args.edges.split(":")
        for edge in sorted(unit_edges(result).get((a, b), [])):
            print(edge)
        return 0
    data = summary(result)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    comps = cycles(result)
    print(f"modules: {data['modules']}  lines: {data['lines']}")
    print(f"root modules: {data['root_modules']}")
    print(
        f"deferred internal imports: {data['deferred_internal_imports']}"
        f" in {data['deferred_in_files']} files"
    )
    print(f"modules over {OVERSIZED_LINES} lines: {data['modules_over_limit']}")
    print(f"argparse outside commands/: {data['argparse_importers_outside_cli']}")
    print(f"textual outside tui/: {data['textual_importers_outside_tui']}")
    print(f"import cycles between units: {len(comps)}")
    for comp in comps:
        print(f"  {len(comp)} units: {', '.join(comp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
