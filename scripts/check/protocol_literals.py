#!/usr/bin/env python3
"""Find diagnostic-protocol bytes written as bare literals outside their named home.

A service ID, response marker, NRC, sub-function or canned request has one home in
``canair.uds`` (``services.py``, ``nrc.py``, ``subfunctions.py``). Everywhere else it is read
by name. This scan finds the places that write such a byte as a literal instead, and
tests/contracts/test_structure.py fails on any of them. The plan that moved the earlier sites
is plans/2026-11-05-named-protocol-constants.md.

Only hex-written integers count (``0x7F``, not ``127``), so timeouts and counts never match.
Four patterns:

* **compare** — a hex literal compared with something that holds a protocol byte: a
  subscript by a constant (``resp[0] == 0x7F``), an ``"nrc"``/``"sid"`` lookup, or a name
  whose words include ``sid``/``nrc``/``service``. ``0x00`` and ``0xFF`` are left out: as
  range bounds they say nothing about the protocol.
* **offset** — ``+ 0x40`` or ``- 0x40``, the positive-response offset, and ``0x40`` as a
  bound (``b < 0x40``), where it is the edge of the response SIDs.
* **constant** — a module- or class-level name with a protocol word in it (``NRC_…``,
  ``…_SID``, ``_READ_SERVICE``, ``SF_…``, ``UDS_…``, ``…_SESSION…``) bound to a hex literal,
  a collection of them or a ``range`` between two.
* **hex string** — a string that is a negative response or TesterPresent request
  (``"7F"``, ``"7F2F78"``, ``"3E00"``). Docstrings are skipped.

The scan is a net, not a proof: a literal it cannot see is still a literal. Masks
(``sub & 0x7F``), byte ranges and data tables are not matched, because they are not
re-declared facts.

Usage:
    uv run python scripts/check/protocol_literals.py           # every finding
    uv run python scripts/check/protocol_literals.py --json    # per-module counts
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
PACKAGE = "canair"
# The named homes: the literals live here, and only here.
HOMES = frozenset({"canair.uds.services", "canair.uds.nrc", "canair.uds.subfunctions"})
RESPONSE_OFFSET = 0x40
RANGE_BOUNDS = frozenset({0x00, 0xFF})
# Words that mark a name as holding a protocol byte.
NAME_WORDS = frozenset(
    {
        "IOCONTROL",
        "IOCP",
        "NEGATIVE",
        "NRC",
        "NRCS",
        "PENDING",
        "POSITIVE",
        "READ",
        "SERVICE",
        "SERVICES",
        "SF",
        "SID",
        "SIDS",
        "SESSION",
        "SESSIONS",
        "UDS",
        "KWP",
    }
)
OPERAND_WORDS = frozenset({"nrc", "sid", "service"})
LOOKUP_KEYS = frozenset({"nrc", "sid"})
HEX_MESSAGE = re.compile(r"^(?:7F(?:[0-9A-F]{2})*|3E[08]0)$", re.IGNORECASE)
HEX_LITERAL = re.compile(r"^0[xX][0-9A-Fa-f]+$")


@dataclass(frozen=True)
class Finding:
    module: str
    line: int
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line}: {self.kind}: {self.text}"


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _Scanner(ast.NodeVisitor):
    def __init__(self, module: str, source: str) -> None:
        self.module = module
        self.source = source
        self.found: list[Finding] = []
        self._docstrings: set[int] = set()
        self._depth = 0  # function nesting; constants count only outside functions

    def _add(self, node: ast.AST, kind: str) -> None:
        text = (ast.get_source_segment(self.source, node) or "").splitlines()[0]
        self.found.append(Finding(self.module, getattr(node, "lineno", 0), kind, text.strip()))

    def _hex(self, node: ast.AST) -> int | None:
        """The value of a hex-written int literal, else ``None``."""
        if not (isinstance(node, ast.Constant) and type(node.value) is int):
            return None
        if not HEX_LITERAL.match(ast.get_source_segment(self.source, node) or ""):
            return None
        return node.value

    def _hex_values(self, node: ast.AST) -> list[int] | None:
        """The ints of a hex literal, a tuple/set/list/frozenset of them or a ``range`` between
        two, else ``None``."""
        single = self._hex(node)
        if single is not None:
            return [single]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "range"
            and len(node.args) == 2
        ):
            bounds = [self._hex(a) for a in node.args]
            return [b for b in bounds if b is not None] if None not in bounds else None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"
            and len(node.args) == 1
        ):
            node = node.args[0]
        if isinstance(node, ast.Tuple | ast.Set | ast.List) and node.elts:
            values = [self._hex(e) for e in node.elts]
            if all(v is not None for v in values):
                return [v for v in values if v is not None]
        return None

    def _mark_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self._docstrings.add(id(body[0].value))

    def visit_Module(self, node: ast.Module) -> None:
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._mark_docstring(node)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._mark_docstring(node)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    @staticmethod
    def _words(name: str) -> set[str]:
        return {w for w in name.split("_") if w}

    def _holds_protocol_byte(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant):
                if type(key.value) is int:
                    return True
                if isinstance(key.value, str) and key.value.lower() in LOOKUP_KEYS:
                    return True
            return False
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value.lower() in LOOKUP_KEYS
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        if isinstance(node, ast.Name | ast.Attribute) and isinstance(name, str):
            return bool(self._words(name.lower()) & OPERAND_WORDS)
        return False

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        literal = [
            v for o in operands for v in (self._hex_values(o) or []) if v not in RANGE_BOUNDS
        ]
        ordering = any(isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE) for op in node.ops)
        if literal and any(self._holds_protocol_byte(o) for o in operands):
            self._add(node, "compare")
        elif ordering and RESPONSE_OFFSET in literal:
            self._add(node, "offset")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add | ast.Sub) and RESPONSE_OFFSET in (
            self._hex(node.left),
            self._hex(node.right),
        ):
            self._add(node, "offset")
        self.generic_visit(node)

    def _constant(self, node: ast.Assign | ast.AnnAssign, targets: list[ast.expr]) -> None:
        if self._depth or node.value is None or self._hex_values(node.value) is None:
            return
        for target in targets:
            if isinstance(target, ast.Name) and self._words(target.id.upper()) & NAME_WORDS:
                self._add(node, "constant")
                return

    def visit_Assign(self, node: ast.Assign) -> None:
        self._constant(node, node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._constant(node, [node.target])
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if (
            isinstance(node.value, str)
            and id(node) not in self._docstrings
            and HEX_MESSAGE.match(node.value)
        ):
            self._add(node, "hex string")


def scan(root: Path | None = None) -> list[Finding]:
    """Every protocol literal outside :data:`HOMES`, in file and line order."""
    root = root or REPO_ROOT / "src" / PACKAGE
    found: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = module_name(path, root)
        if name in HOMES:
            continue
        source = path.read_text(encoding="utf8")
        scanner = _Scanner(name, source)
        scanner.visit(ast.parse(source))
        found += sorted(scanner.found, key=lambda f: f.line)
    return found


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
        return 0
    for f in found:
        print(f)
    print(f"\n{len(found)} protocol literals in {len(counts(found))} modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
