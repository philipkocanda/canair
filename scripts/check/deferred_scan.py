"""Find the function-level imports of canair modules, and what loading a module costs.

Shared by ``import_graph.py --deferred`` (the report), ``test_structure.py`` (the
``deferred_imports_without_reason`` ceiling) and ``scripts/dev/hoist_deferred.py`` (the tool that
hoists them). It reads source only; ``loaded_modules`` is the one function that runs canair.

A *deferred* import here is an import of a canair module that sits inside a function body. An
import under ``if TYPE_CHECKING:`` is not deferred (it never runs), and neither is a module-level
``try`` or ``if`` block, a class body or a ``__future__`` import.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_graph as graph

REASON_MARK = "# deferred:"
# Third-party packages that `canair --help` does not load, and that cost start-up time.
HEAVY_EXTERNAL = ("textual", "cantools", "argcomplete")
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
TRIES = tuple(t for t in (ast.Try, getattr(ast, "TryStar", None)) if t)

_LOADED_SCRIPT = (
    "import json, sys\n"
    "from canair.cli import build_parser\n"
    "build_parser()\n"
    "print(json.dumps(sorted(m for m in sys.modules if m == 'canair' or m.startswith('canair.'))))"
)


@dataclass(frozen=True)
class FunctionImport:
    """One import statement inside a function body."""

    module: str  # the module that contains the statement
    path: Path
    line: int
    end_line: int
    source: str  # `from X import ..`: X, resolved; `import X`: X
    names: tuple[tuple[str, str | None], ...]  # (name, asname)
    level: int  # leading dots of a relative `from` import
    rel_module: str  # the module as written after those dots
    is_from: bool
    targets: tuple[str, ...]  # canair modules that running the statement loads
    in_try: bool
    has_reason: bool
    trailing_comment: str  # the `# ...` after the statement on its last line, or ""

    @property
    def label(self) -> str:
        names = ", ".join(n + (f" as {a}" if a else "") for n, a in self.names)
        return f"from {self.source} import {names}" if self.is_from else f"import {names}"


@dataclass
class Tree:
    """Every module of the package, parsed once."""

    root: Path
    texts: dict[str, str] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)
    imports: list[FunctionImport] = field(default_factory=list)
    # module -> canair modules it imports at module level (parents of each target included)
    edges: dict[str, set[str]] = field(default_factory=dict)
    heavy_direct: set[str] = field(default_factory=set)
    # imports under `if TYPE_CHECKING`, in module-level try/if blocks and class bodies
    other_nested: int = 0

    @property
    def modules(self) -> set[str]:
        return set(self.paths)


def _mentions_type_checking(test: ast.expr) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "TYPE_CHECKING")
        or (isinstance(n, ast.Attribute) and n.attr == "TYPE_CHECKING")
        for n in ast.walk(test)
    )


def walk_imports(tree: ast.AST):
    """Yield ``(node, in_function, in_try, in_type_checking)`` for every import statement."""

    def visit(node: ast.AST, fn: bool, try_: bool, tc: bool):
        for field_name, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if not isinstance(child, ast.AST):
                    continue
                child_tc = tc or (
                    isinstance(node, ast.If)
                    and field_name == "body"
                    and _mentions_type_checking(node.test)
                )
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    yield child, fn, try_, child_tc
                    continue
                yield from visit(
                    child,
                    fn or isinstance(child, FUNCTIONS),
                    try_ or isinstance(child, TRIES),
                    child_tc,
                )

    yield from visit(tree, False, False, False)


def _parents(module: str) -> list[str]:
    parts = module.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def import_targets(node: ast.stmt, module: str, is_package: bool, known: set[str]) -> list[str]:
    """The canair modules that running the import loads, parents last, unknown names dropped."""
    found: list[str] = []
    if isinstance(node, ast.Import):
        found = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        base = graph._resolve(node, module, is_package)
        found = [base] + [f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" in known]
    return [t for t in dict.fromkeys(found) if t in known]


def has_reason(lines: list[str], first: int, last: int) -> bool:
    """True when the statement has a ``# deferred:`` comment on one of its lines or just above.

    Just above means the comment block that ends on the line before the statement, and the
    reason sits on the first line of that block.
    """
    if any(REASON_MARK in line for line in lines[first - 1 : last]):
        return True
    i = first - 2
    while i >= 0 and lines[i].lstrip().startswith("#"):
        if lines[i].lstrip().startswith(REASON_MARK):
            return True
        i -= 1
    return False


def trailing_comment(lines: list[str], node: ast.stmt) -> str:
    """The comment after the statement on its last line (``""`` if none)."""
    rest = lines[node.end_lineno - 1][node.end_col_offset :].strip()  # type: ignore[index]
    return rest if rest.startswith("#") else ""


def scan_text(
    module: str, path: Path, text: str, known: set[str], is_package: bool | None = None
) -> tuple[list[FunctionImport], set[str], bool, int]:
    """``(function-level imports, module-level targets, imports a heavy package, others)``."""
    if is_package is None:
        is_package = path.name == "__init__.py"
    lines = text.split("\n")
    found: list[FunctionImport] = []
    edges: set[str] = set()
    heavy = False
    others = 0
    for node, in_fn, in_try, in_tc in walk_imports(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" and not node.level:
            continue
        if isinstance(node, ast.Import):
            heads = [a.name for a in node.names]
        else:
            heads = [graph._resolve(node, module, is_package)]
        if any(h.split(".")[0] in HEAVY_EXTERNAL for h in heads) and not in_fn and not in_tc:
            heavy = True
        if not any(h.split(".")[0] == graph.PACKAGE for h in heads):
            continue
        targets = import_targets(node, module, is_package, known)
        if in_tc:
            others += 1
        elif not in_fn:
            for t in targets:
                edges.add(t)
                edges.update(_parents(t))
            if in_try or node.col_offset:
                others += 1
        else:
            found.append(
                FunctionImport(
                    module=module,
                    path=path,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    source=(
                        graph._resolve(node, module, is_package)
                        if isinstance(node, ast.ImportFrom)
                        else heads[0]
                    ),
                    names=tuple((a.name, a.asname) for a in node.names),
                    level=getattr(node, "level", 0) or 0,
                    rel_module=getattr(node, "module", None) or "",
                    is_from=isinstance(node, ast.ImportFrom),
                    targets=tuple(targets),
                    in_try=in_try,
                    has_reason=has_reason(lines, node.lineno, node.end_lineno or node.lineno),
                    trailing_comment=trailing_comment(lines, node),
                )
            )
    return found, edges, heavy, others


def scan_tree(root: Path | None = None) -> Tree:
    """Parse every module under the package."""
    root = root or graph.REPO_ROOT / "src" / graph.PACKAGE
    tree = Tree(root=root)
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            name = graph.module_name(path, root)
            tree.paths[name] = path
            tree.texts[name] = path.read_text(encoding="utf8")
    known = tree.modules
    for name, path in tree.paths.items():
        found, edges, heavy, others = scan_text(name, path, tree.texts[name], known)
        tree.imports += found
        tree.edges[name] = edges
        tree.other_nested += others
        if heavy:
            tree.heavy_direct.add(name)
    return tree


def reach(tree: Tree, start: str) -> set[str]:
    """Every module that importing ``start`` loads (itself, its parents and what they import)."""
    seen: set[str] = set()
    queue = deque([start, *_parents(start)])
    while queue:
        mod = queue.popleft()
        if mod in seen or mod not in tree.paths:
            continue
        seen.add(mod)
        queue.extend(tree.edges.get(mod, ()))
        queue.extend(_parents(mod))
    return seen


def is_heavy(tree: Tree, module: str) -> bool:
    """Whether importing the module loads textual, cantools or argcomplete."""
    return bool(reach(tree, module) & tree.heavy_direct)


def loaded_modules(root: Path | None = None) -> set[str]:
    """The canair modules that building the parser loads, from a real run in a subprocess."""
    root = root or graph.REPO_ROOT / "src" / graph.PACKAGE
    with tempfile.TemporaryDirectory() as config_home:
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": config_home,
            "CANAIR_NO_UPDATE_CHECK": "1",
            "PYTHONPATH": os.pathsep.join(
                filter(None, [str(root.parent), os.environ.get("PYTHONPATH")])
            ),
        }
        done = subprocess.run(
            [sys.executable, "-c", _LOADED_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            cwd=config_home,
            check=False,
        )
    if done.returncode:
        raise RuntimeError(f"building the parser failed:\n{done.stderr}")
    return set(json.loads(done.stdout.strip().splitlines()[-1]))


def without_reason(imports: list[FunctionImport]) -> list[FunctionImport]:
    return [i for i in imports if not i.has_reason]
