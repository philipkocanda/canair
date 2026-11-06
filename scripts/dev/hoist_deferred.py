#!/usr/bin/env python3
"""Hoist the ``loaded`` deferred imports of canair modules to module level.

Usage:
    uv run python scripts/dev/hoist_deferred.py core config          # dry run: what would move
    uv run python scripts/dev/hoist_deferred.py core config --write  # do it, then verify

A TARGET is a package or module (``core``, ``cli.commands.scan``, ``canair.uds.echo``) or a path
under ``src/canair``. The tool hoists exactly the imports that ``import_graph.py --deferred``
calls ``loaded``: the parser build has already loaded them, so they cost no start-up time. It
skips ``heavy`` and ``cycle`` imports, imports in a ``try`` block, and any statement it cannot move
without changing behaviour (a name that is also bound at module level, a block it would empty, a
module that would grow past its size ceiling). Each skip prints its reason. It changes nothing but
the moved statements and the import block, and a second run finds nothing left to do.

After ``--write`` it imports every touched module (and every module it now imports) in a fresh
interpreter. Read its "patch targets" list: a test that patches ``canair.a.name`` stops affecting a
module that imported ``name`` at the top, and that test has to patch the importing module instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "check"))

import deferred_hoist as hoist
import deferred_imports as imports
import deferred_scan as scan

REPO = scan.graph.REPO_ROOT
LINE_LIMIT = 500


def size_ceiling(module: str) -> int:
    baseline = REPO / "tests" / "contracts" / "baseline.json"
    data = json.loads(baseline.read_text(encoding="utf8"))
    if module in data["long_modules"]:
        return data["long_modules"][module]["ceiling"]
    return data["oversized_modules"].get(module, LINE_LIMIT)


def patch_targets(hoisted: list[scan.FunctionImport]) -> list[str]:
    """Test lines that patch a name the tool now imports at the top of another module."""
    wanted: dict[str, set[str]] = defaultdict(set)
    for imp in hoisted:
        for name, _asname in imp.names:
            wanted[name].add(f"{imp.source}.{name}")
    found: list[str] = []
    for path in sorted((REPO / "tests").rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf8").split("\n"), 1):
            if not re.search(r"setattr|patch|monkeypatch", line):
                continue
            for name, dotted in wanted.items():
                if any(d in line for d in dotted) or re.search(
                    rf"\(.*[\"']{re.escape(name)}[\"']", line
                ):
                    found.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
                    break
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("targets", nargs="+", metavar="TARGET")
    parser.add_argument("--write", action="store_true", help="write the files (default: dry run)")
    parser.add_argument("--no-verify", action="store_true", help="skip the fresh-import check")
    args = parser.parse_args(argv)

    tree = scan.scan_tree()
    wanted = {m for m in tree.paths if any(imports.matches(m, t) for t in args.targets)}
    if not wanted:
        print(f"no module matches {args.targets}")
        return 1
    verdicts = imports.classify(tree, scan.loaded_modules(), only=wanted)

    selected: dict[str, set[int]] = defaultdict(set)
    for imp, verdict in verdicts.items():
        if imp.module in wanted and verdict.kind == "loaded":
            selected[imp.module].add(imp.line)

    hoisted: list[scan.FunctionImport] = []
    changed: dict[str, str] = {}
    for module in sorted(selected):
        text = tree.texts[module]
        result = hoist.hoist_text(module, tree.paths[module], text, selected[module], tree.modules)
        rel = tree.paths[module].relative_to(REPO)
        for imp, why in result.skipped:
            print(f"skip   {rel}:{imp.line}: {imp.label}\n       because {why}")
        for note in result.notes:
            print(f"note   {note}")
        if result.text == text:
            continue
        before, after = text.count("\n"), result.text.count("\n")
        if after > before and after > size_ceiling(module):
            print(f"refuse {rel}: {before} -> {after} lines is over its ceiling")
            continue
        changed[module] = result.text
        hoisted += result.hoisted + result.dropped
        count = len(result.hoisted) + len(result.dropped)
        print(f"hoist  {rel}: {count} imports, {before} -> {after} lines")

    print(f"\n{len(hoisted)} imports in {len(changed)} files")
    if not args.write:
        print("dry run; pass --write to apply")
        return 0
    for module, text in changed.items():
        tree.paths[module].write_text(text, encoding="utf8")
    targets = patch_targets(hoisted)
    if targets:
        print("\npatch targets (a test may patch the old location; check each):")
        print("\n".join(f"  {t}" for t in targets))
    if args.no_verify or not changed:
        return 0
    entries = sorted(set(changed) | {t for i in hoisted for t in i.targets})
    with ThreadPoolExecutor(max_workers=8) as pool:
        errors = list(pool.map(lambda e: imports.imports_fresh(tree.root, "", None, e), entries))
    broken = [(e, err) for e, err in zip(entries, errors, strict=True) if err]
    for entry, error in broken:
        print(f"BROKEN import {entry}: {error}")
    print(f"verified {len(entries)} modules in fresh interpreters: {len(broken)} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
