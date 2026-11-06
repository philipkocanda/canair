#!/usr/bin/env python3
"""Find library code that reaches the car through the process-global active profile.

Library code below ``canair.cli`` takes the profile as an argument. The global is read by
calling ``active()``, or by calling ``load_pids``, ``load_raw_pids`` or ``revision_resolutions``
with no profile, which each fall back to ``active()``. This scan counts those calls outside
``canair.cli``, and tests/contracts/test_structure.py fails when the count grows. The plan is
plans/2026-11-05-code-organization-3-explicit-profile.md (part B).

Usage:
    uv run python scripts/check/ambient_profile.py           # every finding
    uv run python scripts/check/ambient_profile.py --json    # per-module counts
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "canair"
# The package that holds the active profile, and may read it.
ALLOWED_PREFIX = "canair.cli"
# Loaders that fall back to the active profile when called with no argument.
AMBIENT_LOADERS = frozenset({"load_pids", "load_raw_pids", "revision_resolutions"})


@dataclass(frozen=True)
class Finding:
    module: str
    line: int
    call: str


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _callee(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_ambient(node: ast.Call) -> bool:
    name = _callee(node)
    bare = not node.args and not node.keywords
    if name == "active":
        return bare
    return name in AMBIENT_LOADERS and bare


def scan(root: Path | None = None) -> list[Finding]:
    """Every ambient call outside :data:`ALLOWED_PREFIX`, in file and line order."""
    root = root or REPO_ROOT / "src" / PACKAGE
    found: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = module_name(path, root)
        if module == ALLOWED_PREFIX or module.startswith(ALLOWED_PREFIX + "."):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf8"))):
            if isinstance(node, ast.Call) and _is_ambient(node):
                found.append(Finding(module, node.lineno, str(_callee(node))))
    return sorted(found, key=lambda f: (f.module, f.line))


def counts(found: list[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in found:
        out[f.module] = out.get(f.module, 0) + 1
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print per-module counts as JSON")
    args = parser.parse_args(argv)
    found = scan()
    if args.json:
        print(json.dumps(counts(found), indent=2))
    else:
        for f in found:
            print(f"{f.module}:{f.line}: {f.call}()")
        print(f"{len(found)} ambient profile calls", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
