"""The shell's status line: the app's facts in named zones, laid out for the terminal's width.

A zone is a named group of :class:`~canair.tui.status.StatusItem` facts (a monitor has *link*,
*poll*, *recording* and *car*). The zones keep their order and their place, ``│`` between them,
and one zone can be pinned to the right edge. When the line is too narrow the least important fact
*anywhere on the line* goes first, so a zone shrinks before another loses its alert. Below
:data:`TWO_LINES_BELOW` columns the zones take two lines instead.

Only the exceptions are drawn as :func:`badge` blocks (``PAUSED``, a link fault, an output left
on): the normal state is plain text, so a badge always means "look here".

A click on a zone posts :class:`StatusLine.ZoneClicked`, so an app can make each zone a shortcut
(the monitor's link zone opens its event log).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich.markup import escape
from textual import events
from textual.message import Message
from textual.widgets import Static

from canair.tui.status import StatusItem

#: Below this width the zones take two lines.
TWO_LINES_BELOW = 72

ZONE_SEP = " [dim]│[/] "
ZONE_SEP_WIDTH = 3


def badge(text: str, style: str) -> str:
    """Markup for an exception: ``text`` as a reversed block in ``style``."""
    return f"[reverse {style}] {escape(text)} [/]"


@dataclass(frozen=True)
class Zone:
    name: str
    items: tuple[StatusItem, ...]


@dataclass(frozen=True)
class Span:
    """Where a zone landed on its line: columns ``start`` to ``end`` (exclusive)."""

    start: int
    end: int
    zone: str


def lay_out(left: Sequence[Zone], right: Zone | None, width: int) -> tuple[str, list[Span]]:
    """One line: the ``left`` zones from the left edge, ``right`` pinned to the right edge.

    Drops the lowest-priority item on the whole line (the rightmost of a tie) until the line
    fits; a zone whose items all went is left out with its separator. Returns the markup and
    each zone's column span.
    """
    flat = [(i, it, it.width) for i, zone in enumerate(left) for it in zone.items if it.markup]
    if right is not None:
        flat += [(-1, it, it.width) for it in right.items if it.markup]

    def widths() -> tuple[list[int], int]:
        groups = [[w for j, _, w in flat if j == i] for i in range(len(left))]
        head = [sum(g) + len(g) - 1 for g in groups if g]
        tail = [w for j, _, w in flat if j == -1]
        tail_w = sum(tail) + max(0, len(tail) - 1)
        return head, tail_w

    while True:
        head, tail_w = widths()
        used = sum(head) + ZONE_SEP_WIDTH * max(0, len(head) - 1)
        gap = 2 if head and tail_w else 0
        if used + gap + tail_w <= width or len(flat) <= 1:
            break
        flat.pop(min(range(len(flat)), key=lambda k: (flat[k][1].priority, -k)))

    spans: list[Span] = []
    chunks: list[str] = []
    col = 0
    for i, zone in enumerate(left):
        items = [it for j, it, _ in flat if j == i]
        if not items:
            continue
        w = sum(it.width for it in items) + len(items) - 1
        spans.append(Span(col, col + w, zone.name))
        chunks.append(" ".join(it.markup for it in items))
        col += w + ZONE_SEP_WIDTH
    text = ZONE_SEP.join(chunks)
    tail_items = [it for j, it, _ in flat if j == -1]
    if right is None or not tail_items:
        return text, spans
    pad = max(1, width - used - tail_w)
    spans.append(Span(used + pad, used + pad + tail_w, right.name))
    return f"{text}{' ' * pad}{' '.join(it.markup for it in tail_items)}", spans


def fit_line(
    left: Sequence[StatusItem], right: Sequence[StatusItem], width: int, sep: str = " [dim]·[/] "
) -> str:
    """``left`` from the left edge, ``right`` pinned right, squeezed as one line by priority."""
    sep_w = StatusItem(sep).width
    tagged = [(0, it) for it in left if it.markup] + [(1, it) for it in right if it.markup]
    while len(tagged) > 1:
        used = sum(it.width for _, it in tagged) + sep_w * len(tagged)
        if used <= width:
            break
        tagged.pop(min(range(len(tagged)), key=lambda k: (tagged[k][1].priority, -k)))
    head = sep.join(it.markup for side, it in tagged if side == 0)
    tail = sep.join(it.markup for side, it in tagged if side == 1)
    pad = max(1, width - StatusItem(head).width - StatusItem(tail).width)
    return f"{head}{' ' * pad}{tail}" if tail else head


class StatusLine(Static):
    """The zoned status line; one line, or two below :data:`TWO_LINES_BELOW` columns."""

    DEFAULT_CSS = """
    StatusLine {
        height: auto; padding: 0 1; background: transparent; text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    class ZoneClicked(Message):
        """The user clicked a zone; ``x`` and ``y`` are where it starts, in screen cells."""

        def __init__(self, zone: str, x: int, y: int) -> None:
            super().__init__()
            self.zone = zone
            self.x = x
            self.y = y

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, markup=True)
        self._left: tuple[Zone, ...] = ()
        self._right: Zone | None = None
        self.spans: list[list[Span]] = []

    def set_zones(self, left: Sequence[Zone], right: Zone | None = None) -> None:
        self._left = tuple(left)
        self._right = right
        self._compose_lines()

    def on_resize(self) -> None:
        self._compose_lines()

    def _width(self) -> int:
        width = self.content_size.width
        if width:
            return width
        app_width = self.app.size.width if self.is_attached else 0
        return max(20, (app_width or 80) - 2)

    def _compose_lines(self) -> None:
        width = self._width()
        app_width = self.app.size.width if self.is_attached else width + 2
        if app_width < TWO_LINES_BELOW and len(self._left) > 1:
            split = (len(self._left) + 1) // 2
            lines = [lay_out(self._left[:split], None, width)]
            lines.append(lay_out(self._left[split:], self._right, width))
        else:
            lines = [lay_out(self._left, self._right, width)]
        self.spans = [spans for _, spans in lines]
        self.update("\n".join(text for text, _ in lines))

    def on_click(self, event: events.Click) -> None:
        line = self.spans[event.y] if 0 <= event.y < len(self.spans) else []
        col = event.x - 1  # the left padding
        for span in line:
            if span.start <= col < span.end:
                region = self.content_region
                self.post_message(
                    self.ZoneClicked(span.zone, region.x + span.start, region.y + event.y)
                )
                return
