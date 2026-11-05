"""Coloured, aligned terminal text that follows the stream it goes to.

A :class:`Painter` colours text for one stream and leaves it plain when that stream gets no
colour (:func:`~canair.render.ansi.use_color` decides, for that stream, not stdout's). Padding
is measured on the plain text, so columns stay aligned with colour on or off. Commands that
write to stderr, or to stdout while ``--json`` sends the human report to stderr, build their
text through one of these rather than through the stdout-gated ``ansi.RED`` names.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from canair.render import ansi

# One table cell: its plain text and the ANSI codes to show it in ("" = plain).
Cell = tuple[str, str]

GAP = "  "
INDENT = "    "


class Painter:
    """Colours text for one stream, or leaves it plain when that stream gets none."""

    def __init__(self, out: TextIO):
        self.on = ansi.use_color(out)

    def __call__(self, text: str, code: str) -> str:
        return f"{code}{text}{ansi.raw.RESET}" if self.on and code and text else text

    def heading(self, title: str, *details: str) -> str:
        """A section heading: the title in bold, its details dimmed after it."""
        tail = "".join(f" · {d}" for d in details if d)
        return f"\n  {self(title, ansi.raw.BOLD)}{self(tail, ansi.raw.DIM)}"

    def fields(self, rows: Sequence[tuple[str, str]], indent: str = INDENT) -> list[str]:
        """``key  value`` lines with the keys dimmed and the values aligned."""
        width = max((len(key) for key, _ in rows), default=0)
        return [
            f"{indent}{self(key.ljust(width), ansi.raw.DIM)}{GAP}{value}" for key, value in rows
        ]


def column_widths(rows: Sequence[Sequence[Cell]]) -> list[int]:
    """The width of each column over ``rows`` (the last column is never padded)."""
    widths: list[int] = []
    for row in rows:
        for i, (text, _code) in enumerate(row):
            if i == len(widths):
                widths.append(0)
            widths[i] = max(widths[i], len(text))
    return widths


def render_row(
    row: Sequence[Cell], widths: Sequence[int], paint: Painter, indent: str = INDENT
) -> str:
    """One aligned table row: each cell padded to its column, then coloured."""
    parts = []
    for i, (text, code) in enumerate(row):
        pad = widths[i] - len(text) if i < len(row) - 1 and i < len(widths) else 0
        parts.append(paint(text, code) + " " * pad)
    return (indent + GAP.join(parts)).rstrip()
