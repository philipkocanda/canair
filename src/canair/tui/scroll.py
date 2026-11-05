"""Scroll-position policy shared by canair's scrollable Textual views.

Every one of those views (the `canair monitor` monitor, the `captures --step`
stepper, `decode --plot`, `sniff`) is the same shape: a `VerticalScroll`
wrapping one `Static` body that is repainted in place with `Static.update()`.
Because the widget is never remounted, Textual keeps the container's scroll
offset across a repaint (and re-clamps it when the new content is shorter), so
**the policy is to leave it alone**: a repaint must never move the viewport out
from under someone reading a byte.

The one legitimate exception is a *reveal*: when the user asks to see a
particular row (moving a selection cursor, jumping to a noted capture), that row
has to be brought on screen if it is outside the viewport — and only then.
:func:`reveal_marker` implements exactly that, so the "keep position, except
reveal the target" rule lives in one place instead of once per app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.widgets import Static

from canair.render.glyphs import CURSOR
from canair.tui.keys import bind

if TYPE_CHECKING:
    from textual.screen import Screen

__all__ = [
    "APP_BASE_CSS",
    "OVERLAY_SCROLL_BINDINGS",
    "SELECTION_MARKER",
    "OverlayScroll",
    "marker_line",
    "reveal_marker",
]

#: The shape every full-screen app shares: a transparent screen over a scroll container
#: holding one body. An app adds its own ``#header`` rule.
APP_BASE_CSS = """
Screen { layout: vertical; background: transparent; }
#scroll {
    height: 1fr; scrollbar-gutter: stable; scrollbar-size-vertical: 1; background: transparent;
}
#body { height: auto; padding: 0 1; background: transparent; }
"""

#: The keys that move a read-only overlay's scroll container; see :class:`OverlayScroll`.
OVERLAY_SCROLL_BINDINGS: list[Binding] = [
    *bind("move_down", "scroll_down", show=False),
    *bind("move_up", "scroll_up", show=False),
    *bind("axis_start", "to_top", show=False),
    *bind("axis_end", "to_bottom", show=False),
]

#: The cursor glyph canair's views draw on the selected row/block.
SELECTION_MARKER = CURSOR


def marker_line(body: Static, marker: str = SELECTION_MARKER) -> int | None:
    """Line index of the first line of ``body`` containing ``marker``."""
    rendered = body.render()
    # A Static's render() returns the visualized Content (a str update is
    # visualized into a Content too), which exposes .plain.
    if not isinstance(rendered, Content):
        return None
    return next((i for i, ln in enumerate(rendered.plain.splitlines()) if marker in ln), None)


def reveal_marker(scroll: VerticalScroll, body: Static, marker: str = SELECTION_MARKER) -> bool:
    """Scroll the marked line into view, but only if it is off screen.

    Returns whether the viewport moved. An already-visible marker leaves the
    scroll position untouched — the caller's own reading position wins over
    re-centering something the user can already see.
    """
    line = marker_line(body, marker)
    if line is None:
        return False
    top = int(scroll.scroll_offset.y)
    height = scroll.size.height or 1
    if line < top:
        scroll.scroll_to(y=line, animate=False)
        return True
    if line >= top + height:
        scroll.scroll_to(y=max(0, line - height + 1), animate=False)
        return True
    return False


class OverlayScroll:
    """The scroll actions of a read-only overlay (help, event log, session info).

    A screen mixes this in, lists :data:`OVERLAY_SCROLL_BINDINGS` in its ``BINDINGS`` and
    names its ``VerticalScroll`` in ``SCROLL_ID``. The container takes focus on mount:
    without it ``pgup``/``pgdn`` and the arrows go nowhere in a long overlay.
    """

    SCROLL_ID: ClassVar[str]

    def _scroller(self) -> VerticalScroll:
        return cast("Screen", self).query_one(f"#{self.SCROLL_ID}", VerticalScroll)

    def on_mount(self) -> None:
        self._scroller().focus()

    def action_scroll_down(self) -> None:
        self._scroller().scroll_relative(y=1, animate=False)

    def action_scroll_up(self) -> None:
        self._scroller().scroll_relative(y=-1, animate=False)

    def action_to_top(self) -> None:
        self._scroller().scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self._scroller().scroll_end(animate=False)
