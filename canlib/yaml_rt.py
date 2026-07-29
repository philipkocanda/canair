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
import textwrap
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
# Folding is value-preserving (``>`` folds line breaks back to spaces), and the
# wrap is applied only to the note itself — never by reflowing the whole file.
NOTE_WRAP_WIDTH = 100


def note_should_inline(value: str, prefix_len: int) -> bool:
    """True when a note should render inline (single line) rather than folded.

    ``prefix_len`` is the width consumed by the key + indent before the value
    (e.g. ``len("    notes: ")``). Inline when the value has no embedded newline
    and the whole ``<prefix><value>`` line fits within :data:`NOTE_WRAP_WIDTH`.
    """
    return "\n" not in value.strip("\n") and prefix_len + len(value) <= NOTE_WRAP_WIDTH


def wrap_note_lines(value: str, width: int) -> list[str]:
    """Word-wrap a note body to ``width``, preserving blank-line paragraph breaks.

    Long unbreakable tokens (URLs, hex) are left intact (they may overflow onto
    their own line). The result is folded-scalar-safe: consecutive non-blank
    lines fold back to spaces, so it round-trips to the original text.
    """
    out: list[str] = []
    for ln in value.strip("\n").splitlines() or [""]:
        stripped = ln.rstrip()
        if not stripped:
            out.append("")  # paragraph break (folded scalar → newline)
            continue
        out.extend(
            textwrap.wrap(stripped, width=width, break_long_words=False, break_on_hyphens=False)
            or [""]
        )
    return out


def _fold_positions(value: str, width: int) -> list[int]:
    """Space indices at which to fold a note (for FoldedScalarString).

    Derived from :func:`wrap_note_lines` (textwrap) so ruamel wraps *only this
    scalar* exactly as the text path does — the document's global emit width
    stays high, so no other content reflows. Multi-paragraph notes are handled
    per paragraph: existing newlines stay hard breaks (they render as paragraph
    breaks and are never folded), and wrapping is applied within each paragraph.
    """
    pos: list[int] = []
    seg_start = 0
    for seg in value.split("\n"):
        idx = 0
        for line in wrap_note_lines(seg, width)[:-1]:
            sp = seg.find(" ", idx + len(line))
            if sp == -1:
                break
            pos.append(seg_start + sp)
            idx = sp + 1
        seg_start += len(seg) + 1  # +1 for the split newline
    return pos


def folded(value: str, key_indent: int = 4, key: str = "notes"):
    """Render a free-text value for a ruamel mapping per the shared note policy.

    Returns a plain ``str`` when the note is short enough to sit inline, else a
    :class:`FoldedScalarString` that emits as ``key: >-`` wrapped at
    ~:data:`NOTE_WRAP_WIDTH` columns (paragraph breaks preserved). ``key_indent``
    is how far the key sits from the left margin (e.g. 4 for an identity field).
    The wrap is pinned via ``fold_pos`` so it folds only this scalar; the note
    round-trips to the original text.
    """
    value = str(value)
    prefix_len = key_indent + len(key) + len(": ")
    if note_should_inline(value, prefix_len):
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
