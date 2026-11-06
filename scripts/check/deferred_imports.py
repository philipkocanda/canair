"""Classify every deferred (function-level) import of canair, and report them.

Run through ``scripts/check/import_graph.py --deferred``. Each import gets one kind:

* ``loaded``: everything it imports is already loaded when ``canair --help`` builds the parser
  (a real run, read from ``sys.modules``). Hoisting it costs no start-up time.
* ``heavy``: it loads textual, cantools or argcomplete, which the parser build does not.
* ``cycle``: hoisting it makes ``import canair.<module>`` fail in a fresh interpreter. A module
  can only fail that way if the imported module reaches it through module-level imports, so the
  check runs only for those, with the hoisted source swapped in by an import hook.
* ``other``: anything else (an import inside ``try``, or a module the parser build does not load).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deferred_hoist as hoist
import deferred_scan as scan

KINDS = ("loaded", "heavy", "cycle", "other")
WORKING_CYCLE = "its target imports this module, so hoisting it makes an import cycle"
RISK_MARK = "~"

_CHILD = """
import importlib, importlib.machinery, importlib.util, json, sys
spec = json.load(sys.stdin)
target = spec["module"]

class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        if fullname == target:
            return self.source_to_code(spec["source"].encode(), self.path)
        return super().get_code(fullname)

class Finder:
    def find_spec(self, name, path=None, target_=None):
        if name != target:
            return None
        found = importlib.machinery.PathFinder.find_spec(name, path)
        return importlib.util.spec_from_file_location(
            name, found.origin, loader=Loader(name, found.origin),
            submodule_search_locations=found.submodule_search_locations)

sys.meta_path.insert(0, Finder())
importlib.import_module(spec["entry"])
"""


@dataclass(frozen=True)
class Verdict:
    kind: str
    why: str = ""
    # hoisting it makes a module-level import cycle that still imports today
    risk: bool = False


def _env(root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "CANAIR_NO_UPDATE_CHECK": "1",
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(root.parent), os.environ.get("PYTHONPATH")])
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def imports_fresh(root: Path, module: str, source: str | None, entry: str) -> str:
    """Import ``entry`` in a fresh interpreter, with ``module`` read from ``source`` if given.

    Returns ``""`` on success, else the last line of the error.
    """
    payload = json.dumps({"module": module, "source": source, "entry": entry})
    script = (
        _CHILD
        if source is not None
        else "import importlib,sys;importlib.import_module(sys.argv[1])"
    )
    cmd = [sys.executable, "-c", script] + ([] if source is not None else [entry])
    done = subprocess.run(
        cmd,
        input=payload if source is not None else None,
        capture_output=True,
        text=True,
        env=_env(root),
        cwd=root.parent,
        check=False,
    )
    if done.returncode == 0:
        return ""
    lines = done.stderr.strip().splitlines()
    return lines[-1] if lines else f"exit {done.returncode}"


def hoist_fails(tree: scan.Tree, module: str, imps: list[scan.FunctionImport]) -> str:
    """Why hoisting ``imps`` into ``module`` breaks an import, or ``""`` if nothing breaks."""
    result = hoist.hoist_text(
        module, tree.paths[module], tree.texts[module], {i.line for i in imps}, tree.modules
    )
    entries = [module, *dict.fromkeys(t for i in imps for t in i.targets)]
    for entry in entries:
        error = imports_fresh(tree.root, module, result.text, entry)
        if error:
            return f"import {entry}: {error}"
    return ""


def classify(
    tree: scan.Tree,
    loaded: set[str],
    trial: bool = True,
    only: set[str] | None = None,
    jobs: int = 8,
) -> dict[scan.FunctionImport, Verdict]:
    """A verdict for every deferred import. ``only`` limits the cycle trial to those modules."""
    verdicts: dict[scan.FunctionImport, Verdict] = {}
    suspects: dict[str, list[scan.FunctionImport]] = defaultdict(list)
    for imp in tree.imports:
        if imp.in_try:
            verdicts[imp] = Verdict("other", "it sits in a try block")
            continue
        if imp.targets and all(t in loaded for t in imp.targets):
            kind, why = "loaded", ""
        elif any(scan.is_heavy(tree, t) for t in imp.targets):
            verdicts[imp] = Verdict("heavy", "it loads textual, cantools or argcomplete")
            continue
        else:
            kind, why = "other", "the parser build does not load it"
        verdicts[imp] = Verdict(kind, why)
        if any(imp.module in scan.reach(tree, t) for t in imp.targets):
            if only is None or imp.module in only:
                suspects[imp.module].append(imp)
            if not trial:
                verdicts[imp] = Verdict("cycle", WORKING_CYCLE)

    def check(module: str) -> list[tuple[scan.FunctionImport, str]]:
        imps = suspects[module]
        if not hoist_fails(tree, module, imps):
            return [(i, "") for i in imps]
        return [(i, hoist_fails(tree, module, [i])) for i in imps]

    if trial and suspects:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for checked in pool.map(check, list(suspects)):
                for imp, error in checked:
                    old = verdicts[imp]
                    verdicts[imp] = (
                        Verdict("cycle", error) if error else Verdict(old.kind, old.why, risk=True)
                    )
    return verdicts


def group_key(module: str, depth: int) -> str:
    return ".".join(module.split(".")[1 : 1 + depth]) or "(root)"


def matches(module: str, wanted: str) -> bool:
    wanted = wanted.removeprefix("src/").replace("/", ".").removesuffix(".py").strip(".")
    wanted = wanted.removesuffix(".__init__")
    if not wanted.startswith(scan.graph.PACKAGE):
        wanted = f"{scan.graph.PACKAGE}.{wanted}"
    return module == wanted or module.startswith(wanted + ".")


def table(verdicts: dict[scan.FunctionImport, Verdict], depth: int) -> dict[str, dict[str, int]]:
    rows: dict[str, Counter] = defaultdict(Counter)
    for imp, verdict in verdicts.items():
        row = rows[group_key(imp.module, depth)]
        row[verdict.kind] += 1
        row["total"] += 1
        row["no_reason"] += not imp.has_reason
    return {k: dict(v) for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["total"])}


def format_table(rows: dict[str, dict[str, int]]) -> str:
    head = ["package", *KINDS, "total", "no reason"]
    body = [
        [name, *(str(r.get(k, 0)) for k in KINDS), str(r["total"]), str(r.get("no_reason", 0))]
        for name, r in rows.items()
    ]
    total = ["total", *(str(sum(r.get(k, 0) for r in rows.values())) for k in KINDS)]
    total += [str(sum(r["total"] for r in rows.values()))]
    total += [str(sum(r.get("no_reason", 0) for r in rows.values()))]
    cells = [head, *body, total]
    widths = [max(len(row[i]) for row in cells) for i in range(len(head))]
    out = []
    for n, row in enumerate(cells):
        out.append(
            "  ".join(
                c.ljust(widths[0]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(row)
            )
        )
        if n in (0, len(cells) - 2):
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def format_files(
    verdicts: dict[scan.FunctionImport, Verdict], wanted: str, kind: str | None
) -> str:
    by_file: dict[Path, list[tuple[scan.FunctionImport, Verdict]]] = defaultdict(list)
    for imp, verdict in verdicts.items():
        if matches(imp.module, wanted) and (kind is None or verdict.kind == kind):
            by_file[imp.path].append((imp, verdict))
    out = []
    for path, items in sorted(by_file.items()):
        counts = Counter(v.kind for _i, v in items)
        summary = " ".join(f"{k}={counts[k]}" for k in KINDS if counts[k])
        out.append(f"{path.relative_to(scan.graph.REPO_ROOT)}  ({summary})")
        for imp, verdict in sorted(items, key=lambda iv: iv[0].line):
            kind = verdict.kind + (RISK_MARK if verdict.risk else "")
            why = f"  [{verdict.why}]" if verdict.kind in ("cycle", "other") else ""
            out.append(f"  {imp.line:>5}  {kind:<7} {imp.label}{why}")
    return "\n".join(out) if out else f"no deferred imports match {wanted!r}"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--files", metavar="PKG", help="list the imports of a package or module")
    parser.add_argument("--kind", choices=KINDS, help="with --files: only this kind")
    parser.add_argument("--depth", type=int, default=1, help="package levels in the table")
    parser.add_argument("--no-trial", action="store_true", help="skip the import trial for cycles")


def run(args: argparse.Namespace) -> int:
    tree = scan.scan_tree()
    loaded = scan.loaded_modules()
    only = {m for m in tree.paths if matches(m, args.files)} if args.files else None
    verdicts = classify(tree, loaded, trial=not args.no_trial, only=only)
    if args.files:
        print(format_files(verdicts, args.files, args.kind))
        return 0
    rows = table(verdicts, args.depth)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    files = len({i.module for i in verdicts})
    print(f"function-level imports of canair modules: {len(verdicts)} in {files} files")
    print(
        f"imports that are not deferred (TYPE_CHECKING, module-level try/if, class body): "
        f"{tree.other_nested}"
    )
    print(f"modules loaded by building the parser: {len(loaded)} of {len(tree.paths)}\n")
    print(format_table(rows))
    print("\nno reason = no `# deferred:` comment on the line or just above it.")
    risky = sum(v.risk for v in verdicts.values())
    print(
        f"{risky} imports ({RISK_MARK} in --files) would make a module-level import cycle that "
        "still imports; hoist them only with care."
    )
    return 0
