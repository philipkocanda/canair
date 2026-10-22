"""Shared comment-preserving YAML round-trip helpers.

Both the capture writer (:mod:`canlib.captures`) and the ECU-file writer
(:mod:`canlib.ecus_edit`) append to and edit hand-authored YAML files in place.
We use ruamel.yaml in round-trip mode so existing comments, quoting, and layout
survive writes — only newly appended content is rendered fresh.

All *readers* in the project use PyYAML, a YAML 1.1 parser. We therefore emit
YAML 1.1 so ruamel quotes 1.1-ambiguous scalars (e.g. a ``"14:00:01"`` time that
1.1 would otherwise read as a sexagesimal int, or a ``"yes"`` note read as a
bool). The ``%YAML 1.1`` directive ruamel adds is stripped on write to keep the
files clean; a directive-less file still parses as 1.1 by default.
"""

from __future__ import annotations

import re
from io import StringIO
from typing import TextIO

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import FoldedScalarString

# ── Free-text (notes) rendering policy — shared by both writer subsystems ──────
#
# Both the text-based per-field editors (canlib.pids_edit) and the ruamel
# round-trip writer (canlib.ecus_edit) render free-text fields (notably
# ``notes``) the same way, so the on-disk style can't drift between them:
#   * a short, single-line note stays an inline scalar (``notes: text``);
#   * a longer/multi-line note becomes a folded block scalar (``notes: >-``)
#     word-wrapped at ~NOTE_WRAP_WIDTH columns for readability.
# The wrap is applied only to the note itself — never by reflowing the whole file
# — and only where a folded scalar reads back to exactly the original text (see
# :func:`note_foldable`); anything folding cannot represent exactly is written
# double-quoted with escapes instead, so no note content is ever lost.
NOTE_WRAP_WIDTH = 100

# Characters a YAML reader treats as line breaks besides "\n". A folded scalar
# can't carry them as content, and a bare one inside a plain scalar breaks the line.
_YAML_EXTRA_BREAKS = frozenset("\r\x85\u2028\u2029")

# What PyYAML's reader accepts in a stream at all (yaml.reader.Reader.NON_PRINTABLE).
_NON_PRINTABLE = re.compile(
    "[^\x09\x0a\x0d\x20-\x7e\x85\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)

# A wrap point: one space between two non-space characters. Breaking anywhere
# else (inside a run of spaces, or next to a tab) would change what folds back.
_WRAP_POINT = re.compile(r"(?<=\S) (?=\S)")


def note_should_inline(value: str, prefix_len: int) -> bool:
    """True when a note should render inline (single line) rather than folded.

    ``prefix_len`` is the width consumed by the key + indent before the value
    (e.g. ``len("    notes: ")``). Inline when the value has no embedded newline
    and the whole ``<prefix><value>`` line fits within :data:`NOTE_WRAP_WIDTH`.
    """
    return "\n" not in value.strip("\n") and prefix_len + len(value) <= NOTE_WRAP_WIDTH


def _spaced(line: str) -> bool:
    """A "more-indented" block-scalar line: its line breaks are never folded."""
    return line[:1] in (" ", "\t")


def note_foldable(value: str) -> bool:
    """True when ``value`` renders as a ``>-`` folded scalar that reads back exactly.

    Folding cannot represent: leading/trailing newlines (``-`` chomps them),
    trailing whitespace on a line (invisible, and stripped by editors), a first
    line that starts with whitespace (it would set the block's indentation), a
    line starting with a tab (ruamel's emitter and the readers disagree on it),
    and line-break or non-printable characters other than ``\\n``. Such values
    are written double-quoted instead (:func:`double_quoted_lines`).
    """
    if not value or value != value.strip("\n"):
        return False
    if _NON_PRINTABLE.search(value) or any(c in _YAML_EXTRA_BREAKS for c in value):
        return False
    lines = value.split("\n")
    if _spaced(lines[0]):
        return False
    return all(ln == ln.rstrip(" \t") and not ln.startswith("\t") for ln in lines)


def _wrap_points(line: str, width: int) -> list[int]:
    """Indices of the spaces in ``line`` at which to break it, greedily, at ``width``.

    Only single spaces between non-space characters are candidates, so folding
    each break back to one space reproduces ``line`` exactly. Long unbreakable
    tokens (URLs, hex) are left intact and may overflow onto their own line.
    """
    candidates = [m.start() for m in _WRAP_POINT.finditer(line)]
    points: list[int] = []
    start = i = 0
    while len(line) - start > width and i < len(candidates):
        # The furthest candidate that keeps this line within width — or, when even
        # the nearest overflows (a long token), the nearest.
        j = i
        while j + 1 < len(candidates) and candidates[j + 1] - start <= width:
            j += 1
        points.append(candidates[j])
        start = candidates[j] + 1
        i = j + 1
    return points


def _line_breaks_between(prev: str, nxt: str, newlines: int) -> int:
    """Blank lines a folded scalar needs to carry ``newlines`` breaks from prev to next.

    Between two ordinary lines a lone break folds to a space, so *n* newlines need
    *n* blank lines. A break next to a more-indented line is already literal, so
    there *n* newlines need only *n - 1* blank lines.
    """
    return newlines if not (_spaced(prev) or _spaced(nxt)) else newlines - 1


def wrap_note_lines(value: str, width: int) -> list[str]:
    """Render a note's folded-scalar body lines, wrapped at ``width``.

    Ordinary lines are word-wrapped at single spaces (folding back to one space
    each); more-indented lines (bullets, indented continuations) are kept whole,
    since a break next to one is literal. Newlines become the number of blank
    lines a folded scalar needs to read them back (:func:`_line_breaks_between`).
    Exact for any :func:`note_foldable` value, and the same rendering ruamel gives
    :func:`folded`.
    """
    out: list[str] = []
    prev: str | None = None
    newlines = 0
    for ln in value.split("\n"):
        if not ln:
            newlines += 1
            continue
        if prev is not None:
            out.extend([""] * _line_breaks_between(prev, ln, newlines + 1))
        if _spaced(ln):
            out.append(ln)
        else:
            start = 0
            for p in _wrap_points(ln, width):
                out.append(ln[start:p])
                start = p + 1
            out.append(ln[start:])
        prev, newlines = ln, 0
    return out or [""]


def _fold_positions(value: str, width: int) -> list[int]:
    """Space indices at which to fold a note (for FoldedScalarString).

    The same wrap points :func:`wrap_note_lines` breaks at, so ruamel wraps *only
    this scalar* exactly as the text path does — the document's global emit width
    stays high, so no other content reflows. Existing newlines are never folded,
    and more-indented lines are never wrapped.
    """
    pos: list[int] = []
    seg_start = 0
    for seg in value.split("\n"):
        if not _spaced(seg):
            pos.extend(seg_start + p for p in _wrap_points(seg, width))
        seg_start += len(seg) + 1  # +1 for the split newline
    return pos


_DQ_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\x85": "\\N",
    "\u2028": "\\L",
    "\u2029": "\\P",
}


def _dq_char(c: str) -> str:
    if c in _DQ_ESCAPES:
        return _DQ_ESCAPES[c]
    if _NON_PRINTABLE.match(c) or c == "\ufeff":
        code = ord(c)
        return f"\\x{code:02X}" if code <= 0xFF else f"\\u{code:04X}"
    return c


def double_quoted(value: str) -> str:
    """``value`` as a single-line YAML double-quoted scalar that reads back exactly."""
    return '"' + "".join(_dq_char(c) for c in value) + '"'


def double_quoted_lines(value: str, first_width: int, width: int) -> list[str]:
    """``value`` as a double-quoted scalar wrapped over lines, for text that can't fold.

    The first line holds at most ``first_width`` columns (it follows ``key: ``);
    the rest at most ``width`` (the caller indents them). Every line but the last
    ends in an escaped line break (``\\``), which a reader drops along with the
    next line's leading indentation, so the content is exact: breaks are placed
    after a space where possible, and a space that would open a continuation line
    is escaped (``\\x20``) so it isn't taken for indentation.
    """
    lines: list[str] = []
    cur = ['"']
    cur_len = 1
    limit = first_width
    for tok in (_dq_char(c) for c in value):
        has_content = len(cur) > (0 if lines else 1)
        # One column is reserved for the trailing "\" (or, on the last line, '"').
        if has_content and cur_len + len(tok) + 1 > limit:
            last_space = max((k for k, t in enumerate(cur) if t == " "), default=-1)
            carry = cur[last_space + 1 :] if 0 < last_space < len(cur) - 1 else []
            lines.append("".join(cur[: len(cur) - len(carry)]) + "\\")
            cur, cur_len, limit = carry, sum(map(len, carry)), width
        if not cur and tok == " ":
            tok = "\\x20"
        cur.append(tok)
        cur_len += len(tok)
    lines.append("".join(cur) + '"')
    return lines


def folded(value: str, key_indent: int = 4, key: str = "notes"):
    """Render a free-text value for a ruamel mapping per the shared note policy.

    Returns a plain ``str`` when the note is short enough to sit inline, else a
    :class:`FoldedScalarString` that emits as ``key: >-`` wrapped at
    ~:data:`NOTE_WRAP_WIDTH` columns, with its line breaks kept. ``key_indent``
    is how far the key sits from the left margin (e.g. 4 for an identity field).
    The wrap is pinned via ``fold_pos`` so it folds only this scalar. A value
    folding can't represent exactly (:func:`note_foldable`) is returned as a plain
    ``str`` too, for ruamel to emit in a style that preserves it.
    """
    value = str(value)
    prefix_len = key_indent + len(key) + len(": ")
    if note_should_inline(value, prefix_len) or not note_foldable(value):
        return value
    fs = FoldedScalarString(value)
    # fold_pos pins the wrap points for this scalar only (a ruamel runtime
    # attribute, absent from its type stubs).
    fs.fold_pos = _fold_positions(value, max(20, NOTE_WRAP_WIDTH - key_indent - 2))  # ty: ignore[unresolved-attribute]
    return fs


def round_trip_yaml(sequence: int = 2, offset: int = 0) -> YAML:
    """Return a configured round-trip ``YAML`` instance (matches PyYAML readers).

    ``sequence``/``offset`` set block-sequence indentation. ruamel applies one
    style to the whole document on dump (it does not preserve per-block
    indentation), so callers editing a hand-authored file should pass the style
    that file already uses — see :func:`detect_sequence_indent`. The defaults
    (dash flush with the parent key) match the capture files.
    """
    y = YAML()  # round-trip by default
    y.preserve_quotes = True
    y.width = 4096  # don't wrap long hex payloads / folded notes
    y.indent(mapping=2, sequence=sequence, offset=offset)
    y.version = (1, 1)  # match the PyYAML (1.1) readers' scalar interpretation
    return y


def detect_sequence_indent(text: str) -> tuple[int, int] | None:
    """Detect the block-sequence (``sequence``, ``offset``) style used in ``text``.

    Returns ruamel-style indents derived from the first block-sequence item:
    ``offset`` is how far the ``-`` sits past its parent key, and ``sequence``
    is ``offset + 2`` (content two columns past the dash). Returns ``None`` when
    the document has no block sequence to learn from (caller keeps its default).

    This lets a writer reproduce a file's existing layout instead of imposing
    one — e.g. ECU files indent the dash (4/2) while captures keep it flush
    with the key (2/0).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)-(?:\s|$)", line)
        if not m:
            continue
        dash_indent = len(m.group(1))
        # The sequence's parent is the nearest preceding *bare* mapping key
        # (a line that ends with ':' after stripping any inline comment) at an
        # indent no deeper than the dash. Flush sequences share the key's indent;
        # indented ones sit past it.
        key_indent = 0
        for j in range(i - 1, -1, -1):
            prev = lines[j]
            stripped = prev.strip()
            if not stripped or stripped.startswith("#"):
                continue
            pind = len(prev) - len(prev.lstrip())
            if pind > dash_indent:
                continue
            if re.sub(r"\s+#.*$", "", prev).rstrip().endswith(":"):
                key_indent = pind
                break
        offset = dash_indent - key_indent
        if offset < 0:
            continue
        return offset + 2, offset
    return None


def dump(data, fobj: TextIO, *, sequence: int = 2, offset: int = 0) -> None:
    """Dump ``data`` as YAML, stripping the leading 1.1 version directive."""
    buf = StringIO()
    round_trip_yaml(sequence=sequence, offset=offset).dump(data, buf)
    lines = buf.getvalue().splitlines(keepends=True)
    while lines and (lines[0].startswith("%YAML") or lines[0].strip() == "---"):
        lines.pop(0)
    fobj.write("".join(lines))
