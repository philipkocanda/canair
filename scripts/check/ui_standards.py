#!/usr/bin/env python3
"""Find the places where canair's CLI or TUI output breaks the cli-tui-standards skill.

The rules are in ``.claude/skills/cli-tui-standards/SKILL.md`` and the decisions behind them in
``plans/2026-11-05-cli-tui-standards.md``. Every check here is a net over the syntax tree, not a
proof: it finds a thing the rules forbid (or allow only in one home) and counts it per file.
tests/contracts/test_ui_standards.py holds each count to the ceiling in ``baseline.json``, so a
new deviation fails at once and an old one can only leave.

Checks (name: what it finds, and where it is allowed):

* **raw_escapes** — a string that holds an output escape sequence (colour, cursor, screen
  control). Allowed in ``render/ansi.py`` only. Key-sequence input (``"\\x1b[A"``) is not
  matched.
* **rich_console** — ``Console(...)``. Allowed in ``render/console.py`` (the one factory).
* **prompts** — ``input(...)`` and ``sys.stdin.readline()``. Allowed in ``core/prompt.py`` and
  ``captures/metadata.py``.
* **message_prefixes** — a string that starts ``Error:``, ``ERROR``, ``WARN:``, ``Warning:``,
  ``WARNING``, ``Note:`` or ``NOTE:``. The levels are ``error:``, ``warning:``, ``note:`` and
  ``hint:``, in lower case.
* **stderr_gating** — colouring stderr with stdout-gated names: ``ansi.RED`` (any gated name) or
  ``ansi.c(...)`` inside a ``print(..., file=sys.stderr)``, or gated names passed to
  ``ansi.cerr(...)``.
* **tui_key_hints** — a string in ``src/canair/tui`` that spells out a key (``esc``,
  ``ctrl+s``, ``↑``). Hints are built from the key roles. Allowed in ``keys.py`` and ``help.py``.

Usage:
    uv run python scripts/check/ui_standards.py           # every finding
    uv run python scripts/check/ui_standards.py --json    # per-check, per-file counts
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "canair"

#: The names ``canair.render.ansi`` resolves through its stdout gate.
GATED_NAMES = frozenset({"BOLD", "DIM", "RED", "GREEN", "YELLOW", "CYAN", "RESET"})

# Output escapes: SGR, erase, private modes, and cursor moves that carry a number. A bare
# ``\x1b[A`` or ``\x1b[H`` is a key a terminal sends us, not something we print.
_OUTPUT_ESCAPE = re.compile(r"\x1b\[(?:[0-9;?]*[mJKhl]|[0-9;]+[ABCDHf])")
_BAD_PREFIX = re.compile(r"^\s*(?:Error:|ERROR\b|WARN:|Warning:|WARNING\b|Note:|NOTE:)")
_KEY_HINT = re.compile(r"\b(?:esc|enter|ctrl\+[a-z]|pgup|pgdn)\b|[↑↓]")

# Where each rule is allowed to live, relative to ``src/canair``.
ALLOWED = {
    "raw_escapes": {"render/ansi.py"},
    "rich_console": {"render/console.py"},
    "prompts": {"core/prompt.py", "captures/metadata.py"},
    "message_prefixes": set(),
    "stderr_gating": set(),
    "tui_key_hints": {"tui/keys.py", "tui/help.py"},
}


@dataclass(frozen=True)
class Finding:
    check: str
    path: str  # relative to the repository root
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.check}] {self.text}"


def _strings(tree: ast.AST):
    """Every string constant in the tree with its line, docstrings left out."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_stderr(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "stderr"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _gated_ansi_uses(node: ast.AST) -> list[ast.Attribute]:
    """``ansi.RED`` and friends, and ``ansi.c`` (which gates on stdout), inside ``node``."""
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "ansi"
        and (n.attr in GATED_NAMES or n.attr == "c")
    ]


def _check_file(rel: str, tree: ast.AST) -> list[Finding]:
    found: list[Finding] = []

    def add(check: str, node: ast.AST, text: str) -> None:
        if rel not in ALLOWED[check]:
            found.append(Finding(check, f"src/canair/{rel}", getattr(node, "lineno", 0), text))

    for node in _strings(tree):
        value = node.value
        if _OUTPUT_ESCAPE.search(value):
            add("raw_escapes", node, repr(value[:40]))
        if _BAD_PREFIX.match(value):
            add("message_prefixes", node, repr(value[:50]))
        if rel.startswith("tui/") and _KEY_HINT.search(value):
            add("tui_key_hints", node, repr(value[:60]))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "Console":
            add("rich_console", node, "Console(...)")
        elif name == "input" and isinstance(node.func, ast.Name):
            add("prompts", node, "input(...)")
        elif name == "readline" and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "stdin"
                and isinstance(target.value, ast.Name)
                and target.value.id == "sys"
            ):
                add("prompts", node, "sys.stdin.readline()")
        elif name == "print":
            if any(kw.arg == "file" and _is_stderr(kw.value) for kw in node.keywords):
                for use in _gated_ansi_uses(node):
                    add("stderr_gating", use, f"ansi.{use.attr} written to stderr")
        elif name == "cerr" and len(node.args) >= 2:
            if _gated_ansi_uses(node.args[1]):
                add("stderr_gating", node, "ansi.cerr(...) given a stdout-gated code")
    return found


def scan(root: Path | None = None) -> list[Finding]:
    """Every finding in the tree under ``root`` (default: ``src/canair``)."""
    root = root or SRC
    found: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        found += _check_file(rel, ast.parse(path.read_text(encoding="utf8")))
    return found


def counts(found: list[Finding] | None = None) -> dict[str, dict[str, int]]:
    """``{check: {path: count}}`` for the findings, paths relative to the repository root."""
    result: dict[str, dict[str, int]] = {name: {} for name in ALLOWED}
    for f in found if found is not None else scan():
        per_file = result[f.check]
        per_file[f.path] = per_file.get(f.path, 0) + 1
    return {check: dict(sorted(files.items())) for check, files in result.items()}


def problems(baseline: dict[str, dict[str, int]], now: dict[str, dict[str, int]]) -> list[str]:
    """Where ``now`` is worse than ``baseline``, and where the baseline is stale.

    A file over its ceiling, or a file with findings that the baseline does not list, is a new
    deviation. A baseline entry the tree no longer needs (the count fell) is stale: lower it
    with ``uv run python -m tests.contracts.test_structure --write``.
    """
    out: list[str] = []
    for check in ALLOWED:
        allowed, current = baseline.get(check, {}), now.get(check, {})
        for path, n in current.items():
            ceiling = allowed.get(path, 0)
            if n > ceiling:
                out.append(
                    f"[{check}] {path}: {n} finding(s), ceiling {ceiling}. "
                    f"Fix it (see the cli-tui-standards skill); run "
                    f"`uv run python scripts/check/ui_standards.py | grep {check}` for the lines."
                )
        for path, ceiling in allowed.items():
            if current.get(path, 0) < ceiling:
                out.append(
                    f"[{check}] {path}: down to {current.get(path, 0)} (ceiling {ceiling}). "
                    "Lower the baseline: uv run python -m tests.contracts.test_structure --write"
                )
    return out


def lowered(
    baseline: dict[str, dict[str, int]], now: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    """``baseline`` with every ceiling cut to the current count, never raised."""
    return {
        check: {
            path: min(n, now.get(check, {}).get(path, 0))
            for path, n in sorted(baseline.get(check, {}).items())
            if now.get(check, {}).get(path, 0) > 0
        }
        for check in ALLOWED
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("Checks")[0].strip())
    parser.add_argument("--json", action="store_true", help="per-check, per-file counts")
    args = parser.parse_args(argv)
    found = scan()
    if args.json:
        print(json.dumps(counts(found), indent=2))
    else:
        for f in found:
            print(f)
        print(f"{len(found)} finding(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
