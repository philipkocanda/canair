"""Width-aware status/hint bar shared by canair's Textual TUIs.

Every canair TUI docks a bar at the bottom of the screen holding a long
``cycle 12 · … · ? help · q quit`` line. That line was built as one formatted
string and dropped into a fixed-height :class:`~textual.widgets.Static`, which
fails silently on a narrow terminal (a phone SSH client sits around 45 columns):
Rich soft-wraps the line, the fixed height clips the overflow, and the tail —
including the ``? help`` pointer to the authoritative cheat-sheet — disappears.

So a status line is modelled here as an ordered list of :class:`StatusItem`,
each ranked by how essential it is, and rendered against the terminal's
*current* width: the least essential items drop out first and ``? help`` is the
last thing to go. The ``?`` modal (:mod:`canlib.tui_help`) is the complete key
list, so the bar only has to stay *honest*, never complete.

Use :class:`StatusBar` in place of the hand-rolled docked ``Static``; it
re-composes itself on resize, so the bar adapts while the window is being
dragged rather than only on the next poll tick.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

# Separator drawn between items (dim, so items read as one line of hints).
SEP = " [dim]·[/] "
_SEP_WIDTH = 3  # visible width of SEP (" · ")

# Priority ladder. Higher survives a squeeze longer; equal priorities drop
# right-to-left, so the leading (most contextual) items outlive trailing ones.
P_LOW = 0  # nice-to-have detail (timings, secondary counters)
P_NORMAL = 10  # the default: ordinary hints and counters
P_HIGH = 20  # live state the user acts on (recording, current selection)
P_ESSENTIAL = 30  # alerts and the escape hatches (`? help`, `q quit`)


@dataclass(frozen=True)
class StatusItem:
    """One segment of a status line: Rich markup plus how essential it is."""

    markup: str
    priority: int = P_NORMAL

    @property
    def width(self) -> int:
        """The segment's *visible* width — markup tags excluded."""
        return Text.from_markup(self.markup).cell_len


def compose_status(items: Iterable[StatusItem], width: int) -> str:
    """Join ``items`` into one line's markup, dropping items until it fits ``width``.

    Empty items are ignored. When everything fits, the line is the items in
    order. Otherwise the lowest-priority item is dropped (the rightmost of a
    tie) until the line fits, so an alert or ``? help`` survives a 40-column
    terminal while a poll timing does not. A single item wider than ``width`` is
    still returned — the bar's ``text-overflow`` ellipsizes it rather than
    wrapping it out of view.
    """
    kept = [it for it in items if it.markup]
    if not kept:
        return ""
    widths = [it.width for it in kept]
    # Drop order: lowest priority first, rightmost within a priority.
    droppable = sorted(range(len(kept)), key=lambda i: (kept[i].priority, -i))
    dropped: set[int] = set()
    for idx in droppable:
        live = [i for i in range(len(kept)) if i not in dropped]
        if _line_width(widths, live) <= width or len(live) <= 1:
            break
        dropped.add(idx)
    return SEP.join(kept[i].markup for i in range(len(kept)) if i not in dropped)


def _line_width(widths: Sequence[int], live: Sequence[int]) -> int:
    if not live:
        return 0
    return sum(widths[i] for i in live) + _SEP_WIDTH * (len(live) - 1)


class StatusBar(Static):
    """Bottom-docked status bar that fits its content to the terminal width.

    Feed it one list of :class:`StatusItem` per line via :meth:`set_lines`; the
    bar keeps them and re-composes on every resize. Height is ``auto`` (one row
    per line) and wrapping is off, so the bar can neither wrap its content out
    of the clipped region nor grow unboundedly on a narrow terminal.
    """

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom; height: auto; max-height: 4; padding: 0 1;
        background: transparent; text-wrap: nowrap; text-overflow: ellipsis;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, markup=True)
        self._lines: list[list[StatusItem]] = []

    def set_lines(self, *lines: Sequence[StatusItem]) -> None:
        """Replace the bar's content with one composed line per argument."""
        self._lines = [list(line) for line in lines]
        self._compose_lines()

    def on_resize(self) -> None:
        self._compose_lines()

    def _compose_lines(self) -> None:
        width = self._content_width()
        self.update("\n".join(compose_status(line, width) for line in self._lines))

    def _content_width(self) -> int:
        """Usable width inside the bar's padding (falls back before first layout)."""
        width = self.content_size.width
        if width:
            return width
        app_width = self.app.size.width if self.is_attached else 0
        return max(20, (app_width or 80) - 2)
