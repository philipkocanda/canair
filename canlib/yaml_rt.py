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
