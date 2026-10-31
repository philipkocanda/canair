"""Load a real-world DBC, repairing the dialect quirks cantools rejects.

Hand-maintained DBCs — opendbc above all, whose own parser is lenient — routinely hold
constructs cantools refuses outright, so a whole file fails over one line. When the
strict-grammar parse fails, :func:`load_dbc` rewrites the text with the narrow, named
repairs below and parses again:

- ``header``         — the ``BS_:``/``BU_:`` section lines are missing (cantools needs one
  of them after ``NS_``); both are added, empty.
- ``leading_zero``   — a signal number written without its leading zero (``(.25,-500)``).
- ``identifier``     — a message or signal name starting with a digit (``0_COUNTER``); it
  gets a ``_`` prefix everywhere it is referenced. The only repair a user sees in the
  result, so it is reported by name.
- ``extended_id``    — a 29-bit ID written without the DBC extended flag (bit 31). A
  standard frame cannot exceed ``0x7FF``, so the flag is the only reading.
- ``terminator``     — a ``VAL_`` value table without its closing ``;``.
- ``comment``        — a ``CM_`` statement outside the grammar (no object keyword, no
  signal name, no closing ``;``). Dropped: ``import dbc`` carries no comments.

A file that parses as written is never rewritten. Every repair names its line in the
original file, so the user can check it. The repairs are pure text functions, testable
without cantools; :func:`load_dbc` is the only part that parses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_EXTENDED_FLAG = 0x80000000
_MAX_EXTENDED_ID = 0x1FFFFFFF

_STRING = r'"(?:[^"\\]|\\.)*"'
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_VALID_COMMENT = re.compile(
    rf"CM_\s+(?:(?:BU_|EV_)\s+{_IDENT}\s+|BO_\s+\d+\s+|SG_\s+\d+\s+{_IDENT}\s+)?{_STRING}\s*;",
    re.DOTALL,
)
_COMMENT = re.compile(rf'^CM_\b[^"\n]*(?:{_STRING}\s*;?)?', re.MULTILINE | re.DOTALL)
_MESSAGE = re.compile(r"^(BO_\s+)(\d+)(\s+)(\w+)(\s*:)", re.MULTILINE)
_SIGNAL_LINE = re.compile(r"^([ \t]+SG_\s+)(\w+).*$", re.MULTILINE)
_BARE_DECIMAL = re.compile(r"(?<=[(\[|,])([-+]?)\.(\d)")
_UNCLOSED_VALUE_TABLE = re.compile(
    rf"^VAL_[ \t]+\d+[ \t]+\w+(?:[ \t]+-?\d+[ \t]+{_STRING})+[ \t]*$", re.M
)
# Statements that name a message by its ID, and the token that precedes the ID.
_ID_REFERENCES = re.compile(
    r"^((?:BO_|VAL_|SIG_VALTYPE_|BO_TX_BU_|SG_MUL_VAL_|SIG_GROUP_)\s+"
    r"|(?:CM_|BA_\s+\"\w*\")\s+(?:BO_|SG_)\s+)(\d+)\b",
    re.MULTILINE,
)
# Statements whose leading part (before any string) can name a message or signal.
_NAME_STATEMENTS = re.compile(
    r"^(?:[ \t]*SG_|BO_|VAL_|CM_|BA_|SIG_VALTYPE_|SG_MUL_VAL_|SIG_GROUP_)\b[^\"\n]*",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Repair:
    """One rewrite of the source text, located by its line in the original file."""

    kind: str
    line: int
    detail: str


class DbcLoadError(Exception):
    """cantools cannot parse the DBC, even after the dialect repairs."""


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _repair_leading_zeros(text: str) -> tuple[str, list[Repair]]:
    repairs: list[Repair] = []

    def fix(m: re.Match) -> str:
        fixed = _BARE_DECIMAL.sub(r"\g<1>0.\2", m.group(0))
        if fixed != m.group(0):
            repairs.append(Repair("leading_zero", _line(text, m.start()), f"signal {m.group(2)}"))
        return fixed

    return _SIGNAL_LINE.sub(fix, text), repairs


def _repair_identifiers(text: str) -> tuple[str, list[Repair]]:
    bad: dict[str, int] = {}
    for m in _MESSAGE.finditer(text):
        if m.group(4)[0].isdigit():
            bad.setdefault(m.group(4), _line(text, m.start(4)))
    for m in _SIGNAL_LINE.finditer(text):
        if m.group(2)[0].isdigit():
            bad.setdefault(m.group(2), _line(text, m.start(2)))
    if not bad:
        return text, []
    token = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, bad)) + r")(?![\w.])")

    def rename(m: re.Match) -> str:
        return token.sub(r"_\1", m.group(0))

    repairs = [Repair("identifier", line, f"{name} -> _{name}") for name, line in bad.items()]
    return _NAME_STATEMENTS.sub(rename, text), repairs


def _repair_extended_ids(text: str) -> tuple[str, list[Repair]]:
    unflagged = {
        int(m.group(2))
        for m in _MESSAGE.finditer(text)
        if 0x7FF < int(m.group(2)) <= _MAX_EXTENDED_ID
    }
    if not unflagged:
        return text, []
    repairs = [
        Repair("extended_id", _line(text, m.start(2)), f"{m.group(4)} 0x{int(m.group(2)):X}")
        for m in _MESSAGE.finditer(text)
        if int(m.group(2)) in unflagged
    ]

    def flag(m: re.Match) -> str:
        fid = int(m.group(2))
        return m.group(1) + (str(fid | _EXTENDED_FLAG) if fid in unflagged else m.group(2))

    return _ID_REFERENCES.sub(flag, text), repairs


def _repair_comments(text: str) -> tuple[str, list[Repair]]:
    repairs: list[Repair] = []

    def drop(m: re.Match) -> str:
        if _VALID_COMMENT.fullmatch(m.group(0)):
            return m.group(0)
        repairs.append(Repair("comment", _line(text, m.start()), m.group(0).split("\n")[0][:60]))
        return "\n" * m.group(0).count("\n")  # keep later line numbers aligned

    return _COMMENT.sub(drop, text), repairs


def _repair_value_tables(text: str) -> tuple[str, list[Repair]]:
    repairs: list[Repair] = []

    def close(m: re.Match) -> str:
        repairs.append(Repair("terminator", _line(text, m.start()), m.group(0)[:40]))
        return m.group(0) + " ;"

    return _UNCLOSED_VALUE_TABLE.sub(close, text), repairs


def _repair_header(text: str) -> tuple[str, list[Repair]]:
    # Without an NS_ section cantools needs neither line; only NS_ followed directly
    # by BO_ trips it.
    if not re.search(r"^NS_\s*:", text, re.MULTILINE):
        return text, []
    if re.search(r"^(?:BS_|BU_)\s*:", text, re.MULTILINE):
        return text, []
    first = re.search(r"^(?:VAL_TABLE_|BO_)\s", text, re.MULTILINE)
    if first is None:
        return text, []
    at = first.start()
    repair = Repair("header", _line(text, at), "added empty BS_: and BU_: sections")
    return text[:at] + "BS_:\n\nBU_:\n\n" + text[at:], [repair]


# Header last: it is the one repair that adds lines, so the others see original numbering.
_REPAIRS = (
    _repair_leading_zeros,
    _repair_identifiers,
    _repair_extended_ids,
    _repair_value_tables,
    _repair_comments,
    _repair_header,
)


def repair_dbc_text(text: str) -> tuple[str, list[Repair]]:
    """Apply every dialect repair to ``text``; return the new text and what changed."""
    repairs: list[Repair] = []
    for step in _REPAIRS:
        text, done = step(text)
        repairs.extend(done)
    return text, sorted(repairs, key=lambda r: (r.line, r.kind))


def load_dbc(path: Path):
    """Parse ``path`` with cantools (non-strict), repairing the text only if it must.

    Returns ``(database, repairs)``; ``repairs`` is empty when the file parsed as
    written. Raises :class:`DbcLoadError` with the original parse error (and what was
    tried) when the repaired text does not parse either.
    """
    import cantools

    try:
        return cantools.database.load_file(str(path), strict=False), []
    except Exception as first:  # cantools raises several unrelated types
        original = first
    # cantools' own default encoding for .dbc files.
    text = path.read_bytes().decode("cp1252", errors="replace")
    fixed, repairs = repair_dbc_text(text)
    if not repairs:
        raise DbcLoadError(str(original))
    try:
        return cantools.database.load_string(fixed, "dbc", strict=False), repairs
    except Exception as second:
        kinds = ", ".join(sorted({r.kind for r in repairs}))
        raise DbcLoadError(
            f"{original} (still unparseable after repairing: {kinds}: {second})"
        ) from second


def summarize(repairs: list[Repair], limit: int = 5) -> list[str]:
    """Human lines for a repair list: one per kind, with its first few locations."""
    by_kind: dict[str, list[Repair]] = {}
    for r in repairs:
        by_kind.setdefault(r.kind, []).append(r)
    lines = []
    for kind, items in by_kind.items():
        shown = "; ".join(f"line {r.line}: {r.detail}" for r in items[:limit])
        more = f"; +{len(items) - limit} more" if len(items) > limit else ""
        lines.append(f"{kind} ({len(items)}): {shown}{more}")
    return lines
