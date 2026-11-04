"""Structure gates: the package layout may get better, never worse.

See plans/2026-11-04-package-layout-and-layering.md (section 9) for the rules these enforce and
docs/development/architecture.md for where code belongs. The import contracts themselves are in
``[tool.importlinter]`` in pyproject.toml and run through ``lint-imports``; this module adds what
a contract cannot say, and runs ``lint-imports`` so plain ``pytest`` is a complete gate.

Each check compares the tree with ``baseline.json``:

* a **list** baseline (root modules, test-to-test imports, packages without a charter) fails on a
  new entry *and* on an entry that no longer applies, so a fix has to leave the baseline;
* a **ceiling** baseline (module sizes, deferred imports, import-linter debt) fails when the
  number grows. Lower it when it falls: ``uv run python -m tests.contracts.test_structure --write``.

Raising a baseline is a decision for the user, and it shows in the diff.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline.json")
CHARTER_MARKERS = ("Belongs:", "Not here:", "Imports:")
MODULE_LINE_LIMIT = 500


def _load_graph():
    path = REPO / "scripts" / "check" / "import_graph.py"
    spec = importlib.util.spec_from_file_location("import_graph", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_graph"] = module
    spec.loader.exec_module(module)
    return module


graph = _load_graph()


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf8"))


def _packages() -> dict[str, Path]:
    """Every package under canlib (not the root), by dotted name."""
    root = REPO / graph.PACKAGE
    return {
        graph.module_name(init, root): init
        for init in sorted(root.rglob("__init__.py"))
        if init.parent != root and "__pycache__" not in init.parts
    }


def _has_charter(init: Path) -> bool:
    doc = ast.get_docstring(ast.parse(init.read_text(encoding="utf8"))) or ""
    return all(re.search(rf"^{re.escape(m)}", doc, re.MULTILINE) for m in CHARTER_MARKERS)


def _test_to_test_imports() -> list[str]:
    """``tests/test_a.py -> tests/test_b.py`` for every test module importing another."""
    found = []
    tests = REPO / "tests"
    for path in sorted(tests.rglob("test_*.py")):
        if "contracts" in path.parts:
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf8"))):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names = [base] if node.level == 0 else [f".{base}" if base else ""]
                names += [f"{base}.{a.name}" for a in node.names]
                if node.level:
                    names += [f".{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                leaf = name.rsplit(".", 1)[-1]
                if leaf.startswith("test_") or leaf == "conftest":
                    found.append(f"{rel} -> {name.lstrip('.')}")
    return sorted(set(found))


def _ignore_import_count() -> int:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf8"))
    contracts = data["tool"]["importlinter"].get("contracts", [])
    return sum(len(c.get("ignore_imports", [])) for c in contracts)


def _diff(kind: str, now: set[str], allowed: set[str], fix: str) -> list[str]:
    problems = [f"{kind}: new entry {item!r}. {fix}" for item in sorted(now - allowed)]
    problems += [
        f"{kind}: {item!r} no longer applies; remove it from tests/contracts/baseline.json"
        for item in sorted(allowed - now)
    ]
    return problems


@pytest.fixture(scope="module")
def scan():
    return graph.scan()


def test_import_contracts_hold():
    """Run ``lint-imports``: the layer and boundary contracts in pyproject.toml."""
    from importlinter.cli import lint_imports

    assert lint_imports(config_filename=str(REPO / "pyproject.toml")) == 0


def test_no_new_module_at_the_package_root(scan):
    problems = _diff(
        "root module",
        set(graph.root_modules(scan)),
        set(_baseline()["root_modules"]),
        "Put the code in the package the placement table names "
        "(docs/development/architecture.md), not at the root.",
    )
    assert not problems, "\n".join(problems)


def _root_reexports() -> int:
    """Top-level imports in ``canlib/__init__.py`` that reach into the package's own modules."""
    tree = ast.parse((REPO / graph.PACKAGE / "__init__.py").read_text(encoding="utf8"))
    return sum(
        1
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and (n.level or (n.module or "").startswith(graph.PACKAGE))
    )


def test_root_init_only_defines_the_version():
    count, ceiling = _root_reexports(), _baseline()["root_init_internal_imports"]
    assert count <= ceiling, (
        f"canlib/__init__.py imports from {count} submodules (ceiling {ceiling}). "
        "The root must not import the packages beneath it."
    )


def test_modules_stay_small(scan):
    limit = MODULE_LINE_LIMIT
    ceilings = _baseline()["oversized_modules"]
    problems = []
    for module, lines in graph.oversized(scan, limit).items():
        if module not in ceilings:
            problems.append(f"{module} is {lines} lines (limit {limit}). Split it by concern.")
        elif lines > ceilings[module]:
            problems.append(f"{module} grew to {lines} lines (ceiling {ceilings[module]}).")
    for module in ceilings:
        if scan.lines.get(module, 0) <= limit:
            problems.append(f"{module} is within {limit} lines now; remove it from the baseline.")
    assert not problems, "\n".join(problems)


def test_deferred_imports_do_not_grow(scan):
    total = sum(scan.deferred.values())
    ceiling = _baseline()["deferred_internal_imports"]
    assert total <= ceiling, (
        f"{total} imports of canlib modules sit inside functions or `if` blocks (ceiling "
        f"{ceiling}). A deferred import needs a measured reason; otherwise import at the top and "
        "fix the layering."
    )


def test_import_linter_debt_does_not_grow():
    count = _ignore_import_count()
    ceiling = _baseline()["import_linter_ignores"]
    assert count <= ceiling, (
        f"{count} ignore_imports entries (ceiling {ceiling}). Fix the import instead of listing "
        "it; if that is impossible, ask the user."
    )


def test_every_package_has_a_charter():
    packages = _packages()
    uncharted = set(_baseline()["packages_without_charter"])
    problems = []
    for name, init in packages.items():
        chartered = _has_charter(init)
        if not chartered and name not in uncharted:
            problems.append(
                f"{name} has no charter. Its __init__ docstring needs the lines "
                f"{', '.join(CHARTER_MARKERS)}"
            )
        if chartered and name in uncharted:
            problems.append(f"{name} has a charter now; remove it from the baseline.")
    problems += [
        f"{name} is gone; remove it from the baseline."
        for name in sorted(uncharted - set(packages))
    ]
    assert not problems, "\n".join(problems)


def test_tests_do_not_import_other_tests():
    problems = _diff(
        "test import",
        set(_test_to_test_imports()),
        set(_baseline()["test_to_test_imports"]),
        "Move the shared code into tests/support/.",
    )
    assert not problems, "\n".join(problems)


def _fresh_baseline(old: dict) -> dict:
    """The baseline as the tree is now, refusing to add anything."""
    result = graph.scan()
    new = dict(old)
    new["root_modules"] = [m for m in old["root_modules"] if m in set(graph.root_modules(result))]
    new["oversized_modules"] = {
        m: min(n, result.lines[m])
        for m, n in old["oversized_modules"].items()
        if result.lines.get(m, 0) > MODULE_LINE_LIMIT
    }
    new["deferred_internal_imports"] = min(
        old["deferred_internal_imports"], sum(result.deferred.values())
    )
    new["root_init_internal_imports"] = min(old["root_init_internal_imports"], _root_reexports())
    new["import_linter_ignores"] = min(old["import_linter_ignores"], _ignore_import_count())
    packages = _packages()
    new["packages_without_charter"] = [
        p
        for p in old["packages_without_charter"]
        if p in packages and not _has_charter(packages[p])
    ]
    current_tests = set(_test_to_test_imports())
    new["test_to_test_imports"] = [t for t in old["test_to_test_imports"] if t in current_tests]
    return new


def _initial_baseline() -> dict:
    result = graph.scan()
    packages = _packages()
    return {
        "root_modules": graph.root_modules(result),
        "root_init_internal_imports": _root_reexports(),
        "oversized_modules": graph.oversized(result, MODULE_LINE_LIMIT),
        "deferred_internal_imports": sum(result.deferred.values()),
        "import_linter_ignores": _ignore_import_count(),
        "packages_without_charter": sorted(p for p, i in packages.items() if not _has_charter(i)),
        "test_to_test_imports": _test_to_test_imports(),
    }


if __name__ == "__main__":
    if "--init" in sys.argv:
        data = _initial_baseline()
    elif "--write" in sys.argv:
        data = _fresh_baseline(_baseline())
    else:
        raise SystemExit("usage: python -m tests.contracts.test_structure (--write | --init)")
    BASELINE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf8")
    print(f"wrote {BASELINE.relative_to(REPO)}")
