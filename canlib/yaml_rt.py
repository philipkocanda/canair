"""Shared comment-preserving YAML round-trip helpers.

Both the capture writer (:mod:`canlib.captures`) and the ECU-registry writer
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

from io import StringIO
from typing import TextIO

from ruamel.yaml import YAML


def round_trip_yaml() -> YAML:
    """Return a configured round-trip ``YAML`` instance (matches PyYAML readers)."""
    y = YAML()  # round-trip by default
    y.preserve_quotes = True
    y.width = 4096  # don't wrap long hex payloads / folded notes
    y.indent(mapping=2, sequence=2, offset=0)
    y.version = (1, 1)  # match the PyYAML (1.1) readers' scalar interpretation
    return y


def dump(data, fobj: TextIO) -> None:
    """Dump ``data`` as YAML, stripping the leading 1.1 version directive."""
    buf = StringIO()
    round_trip_yaml().dump(data, buf)
    lines = buf.getvalue().splitlines(keepends=True)
    while lines and (lines[0].startswith("%YAML") or lines[0].strip() == "---"):
        lines.pop(0)
    fobj.write("".join(lines))
