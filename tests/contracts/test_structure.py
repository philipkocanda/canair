"""Structure gates: the package layout may get better, never worse.

See plans/2026-11-04-package-layout-and-layering.md (section 9) for the rules these enforce and
docs/development/architecture.md for where code belongs. The import contracts themselves are in
``[tool.importlinter]`` in pyproject.toml and run through ``lint-imports``; this module adds what
a contract cannot say, and runs ``lint-imports`` so plain ``pytest`` is a complete gate.

Each check compares the tree with ``baseline.json``:

* a **list** baseline (root modules, flat tests, packages without a charter, private names used
  across packages) fails on a new entry *and* on an entry that no longer applies, so a fix has
  to leave the baseline;
* a **ceiling** baseline (module sizes, deferred imports and those without a ``# deferred:``
  reason, the modules that building the parser loads, import-linter debt) fails when the number
  grows. Lower it when it falls: ``uv run python -m tests.contracts.test_structure --write``.

One check has no baseline: a protocol byte written as a literal outside ``canair.uds``.
The calls that read the process-global active profile outside ``canair.cli`` have a ceiling
(``ambient_profile_calls``, scripts/check/ambient_profile.py).
The CLI/TUI output rules (``scripts/check/ui_standards.py``, tested in test_ui_standards.py) keep
a per-file ceiling for each check under ``ui_standards``; ``--write`` lowers those too.

Raising a baseline is a decision for the user, and it shows in the diff.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline.json")
CHARTER_MARKERS = ("Belongs:", "Not here:", "Imports:")
MODULE_LINE_LIMIT = 500
RESERVED_TEST_DIRS = {"support", "integration", "contracts"}
TEST_DATA_DIRS = {"fixtures", "js"}
# Directories that mirror something other than a canair package.
OTHER_MIRRORS = {"scripts"}


def _load_check(name: str):
    path = REPO / "scripts" / "check" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


graph = _load_check("import_graph")
protocol_literals = _load_check("protocol_literals")
ambient_profile = _load_check("ambient_profile")
ui_standards = _load_check("ui_standards")
deferred = _load_check("deferred_scan")
agent_guide = _load_check("agent_guide")


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf8"))


def _packages() -> dict[str, Path]:
    """Every package under canair (not the root), by dotted name."""
    root = REPO / "src" / graph.PACKAGE
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


def _test_dirs() -> list[Path]:
    return sorted(
        d for d in (REPO / "tests").iterdir() if d.is_dir() and not d.name.startswith(("_", "."))
    )


def _flat_tests() -> list[str]:
    """Test files directly in ``tests/``: tests of a module that is not in a package yet."""
    return sorted(p.name for p in (REPO / "tests").glob("test_*.py"))


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
    """Run ``lint-imports``: the layer and boundary contracts in pyproject.toml.

    In a subprocess: import-linter reconfigures logging and terminal state for the whole
    process, which would leak into the tests that run after this one on the same worker.
    """
    result = subprocess.run(
        [sys.executable, "-c", "from importlinter.cli import lint_imports_command as m; m()"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
    """Top-level imports in ``src/canair/__init__.py`` that reach into the package's own modules."""
    tree = ast.parse((REPO / "src" / graph.PACKAGE / "__init__.py").read_text(encoding="utf8"))
    return sum(
        1
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and (n.level or (n.module or "").startswith(graph.PACKAGE))
    )


def test_root_init_only_defines_the_version():
    count, ceiling = _root_reexports(), _baseline()["root_init_internal_imports"]
    assert count <= ceiling, (
        f"src/canair/__init__.py imports from {count} submodules (ceiling {ceiling}). "
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


def test_no_protocol_byte_is_written_as_a_literal():
    """Protocol bytes are read by name from ``canair.uds``, never written as literals.

    The patterns are in scripts/check/protocol_literals.py, the migration that emptied the
    tree of them in plans/2026-11-05-named-protocol-constants.md.
    """
    found = protocol_literals.scan()
    assert not found, (
        "Read these bytes by name from canair.uds (services.py, nrc.py, subfunctions.py), and "
        "add the name there if it does not exist:\n" + "\n".join(f"    {f}" for f in found)
    )


def test_library_code_does_not_read_the_active_profile():
    """Library code takes the profile as an argument; only ``canair.cli`` holds the active one.

    The scan is scripts/check/ambient_profile.py, the migration is
    plans/2026-11-05-code-organization-3-explicit-profile.md (part B).
    """
    found = ambient_profile.scan()
    ceiling = _baseline()["ambient_profile_calls"]
    assert len(found) <= ceiling, (
        f"{len(found)} calls read the active profile outside canair.cli (ceiling {ceiling}). "
        "Take a `profile` argument and let the command pass it:\n"
        + "\n".join(f"    {f.module}:{f.line}: {f.call}()" for f in found)
    )


def test_deferred_imports_do_not_grow(scan):
    total = sum(scan.deferred.values())
    ceiling = _baseline()["deferred_internal_imports"]
    assert total <= ceiling, (
        f"{total} imports of canair modules sit inside functions or `if` blocks (ceiling "
        f"{ceiling}). A deferred import needs a measured reason; otherwise import at the top and "
        "fix the layering."
    )


def _deferred_without_reason() -> list:
    return deferred.without_reason(deferred.scan_tree().imports)


def test_deferred_imports_state_a_reason():
    """A function-level import of a canair module has a ``# deferred:`` reason, or is at the top.

    ``python scripts/check/import_graph.py --deferred --files PKG`` lists them by kind, and
    ``scripts/dev/hoist_deferred.py PKG`` hoists the ones that cost no start-up time.
    """
    found = _deferred_without_reason()
    ceiling = _baseline()["deferred_imports_without_reason"]
    assert len(found) <= ceiling, (
        f"{len(found)} function-level imports of canair modules have no `# deferred: <reason>` "
        f"comment on the line or just above it (ceiling {ceiling}). Import at the top of the "
        "module, or say why the import waits (a measured start-up cost, an optional dependency, "
        "or `cycle with <package>, see plans/<plan>.md`). New ones:\n"
        + "\n".join(f"    {i.path.relative_to(REPO)}:{i.line}: {i.label}" for i in found[-10:])
    )


def test_parser_build_does_not_load_more_modules():
    """Every command builds the whole parser before it runs, so start-up pays for each module.

    A count, not a time: it gives the same result on every machine, and it catches the small
    steps that a timing threshold misses. From 2026-11-04 to 2026-11-06 the count went from 334
    to 637 modules and start-up rose by about 70 ms, in steps of 3 to 15 ms each.
    """
    count = len(deferred.loaded_modules())
    ceiling = _baseline()["parser_build_modules"]
    assert count <= ceiling, (
        f"Building the parser loads {count} canair modules (ceiling {ceiling}), and every command "
        "pays for each one at start-up. Import what only a command's `run` needs inside it, with "
        "a `# deferred: <measured start-up cost>` comment. To see what loads: python -X importtime "
        '-c "from canair.cli import build_parser; build_parser()"'
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


def test_no_package_imports_a_private_name_of_another():
    problems = _diff(
        "private import",
        set(graph.cross_package_private_imports()),
        set(_baseline()["cross_package_private_imports"]),
        "Another package uses it, so give the name no leading underscore (or move it).",
    )
    assert not problems, "\n".join(problems)


def test_tests_do_not_import_other_tests():
    problems = _diff(
        "test import",
        set(_test_to_test_imports()),
        set(_baseline()["test_to_test_imports"]),
        "Move the shared code into tests/support/.",
    )
    assert not problems, "\n".join(problems)


def test_test_directories_mirror_the_source_tree():
    packages = {name.split(".")[1] for name in _packages()}
    allowed = packages | OTHER_MIRRORS | RESERVED_TEST_DIRS | TEST_DATA_DIRS
    problems = [
        f"tests/{d.name}/ matches no package under src/canair/. Mirror a package, or use one of "
        f"{sorted(RESERVED_TEST_DIRS)}."
        for d in _test_dirs()
        if d.name not in allowed
    ]
    problems += [
        f"canair.{name} uses the reserved test directory name {name!r}; rename the package."
        for name in sorted(packages & (RESERVED_TEST_DIRS | TEST_DATA_DIRS))
    ]
    assert not problems, "\n".join(problems)


def test_mirrored_tests_import_their_package():
    """A test in ``tests/<pkg>/`` tests ``canair.<pkg>``; one with another subject lives elsewhere."""
    mirrored = {name.split(".")[1] for name in _packages()}
    problems = []
    for d in _test_dirs():
        if d.name not in mirrored:
            continue
        own = f"{graph.PACKAGE}.{d.name}"
        for path in sorted(d.rglob("test_*.py")):
            modules: list[str] = []
            for node in ast.walk(ast.parse(path.read_text(encoding="utf8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.Import):
                    modules += [a.name for a in node.names]
            if not any(m == own or m.startswith(own + ".") for m in modules):
                problems.append(
                    f"{path.relative_to(REPO).as_posix()} never imports {own}. Move it to the "
                    "directory of the package it tests (tests/integration/ if it spans several)."
                )
    assert not problems, "\n".join(problems)


def test_no_new_test_file_at_the_tests_root():
    problems = _diff(
        "flat test",
        set(_flat_tests()),
        set(_baseline()["flat_tests"]),
        "Put it in the tests/ directory that mirrors the package it tests.",
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
    new["deferred_imports_without_reason"] = min(
        old["deferred_imports_without_reason"], len(_deferred_without_reason())
    )
    new["root_init_internal_imports"] = min(old["root_init_internal_imports"], _root_reexports())
    new["parser_build_modules"] = min(old["parser_build_modules"], len(deferred.loaded_modules()))
    new["ambient_profile_calls"] = min(old["ambient_profile_calls"], len(ambient_profile.scan()))
    new["import_linter_ignores"] = min(old["import_linter_ignores"], _ignore_import_count())
    packages = _packages()
    new["packages_without_charter"] = [
        p
        for p in old["packages_without_charter"]
        if p in packages and not _has_charter(packages[p])
    ]
    new["flat_tests"] = [t for t in old["flat_tests"] if t in set(_flat_tests())]
    current_tests = set(_test_to_test_imports())
    new["test_to_test_imports"] = [t for t in old["test_to_test_imports"] if t in current_tests]
    now = set(graph.cross_package_private_imports())
    new["cross_package_private_imports"] = [
        n for n in old["cross_package_private_imports"] if n in now
    ]
    new["ui_standards"] = ui_standards.lowered(old.get("ui_standards", {}), ui_standards.counts())
    guide = agent_guide.counts()
    new["agents_md_lines"] = min(old["agents_md_lines"], guide["lines"])
    new["agents_md_code_paths"] = min(old["agents_md_code_paths"], guide["code_paths"])
    return new


def _initial_baseline() -> dict:
    result = graph.scan()
    packages = _packages()
    return {
        "root_modules": graph.root_modules(result),
        "root_init_internal_imports": _root_reexports(),
        "oversized_modules": graph.oversized(result, MODULE_LINE_LIMIT),
        "deferred_internal_imports": sum(result.deferred.values()),
        "deferred_imports_without_reason": len(_deferred_without_reason()),
        "parser_build_modules": len(deferred.loaded_modules()),
        "ambient_profile_calls": len(ambient_profile.scan()),
        "import_linter_ignores": _ignore_import_count(),
        "packages_without_charter": sorted(p for p, i in packages.items() if not _has_charter(i)),
        "test_to_test_imports": _test_to_test_imports(),
        "flat_tests": _flat_tests(),
        "cross_package_private_imports": graph.cross_package_private_imports(),
        "ui_standards": ui_standards.counts(),
        "agents_md_lines": agent_guide.counts()["lines"],
        "agents_md_code_paths": agent_guide.counts()["code_paths"],
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
