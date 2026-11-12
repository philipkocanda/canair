"""The shell's bottom line: the current message, or the context and the routes to every command.

The left side is the app's *context*: what is selected and the keys of the pane that has focus,
plus any mode that is not the default. The right side is the *routes*, the keys that reach every
command (``F10 menu · ctrl+p commands · ? help``), so a newcomer always sees where to look. A
message (:mod:`.messages`) replaces the context while it lasts; the routes stay.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.markup import escape
from textual.widgets import Static

from canair.tui.keys import key_hint
from canair.tui.status import P_ESSENTIAL, P_HIGH, P_NORMAL, StatusItem, hint_item

from .statusline import fit_line

#: Above every context item, so a message is the last thing on the left to go.
P_MESSAGE = P_ESSENTIAL + 10


def routes() -> list[StatusItem]:
    """The keys that reach every command. ``? help`` outlives the other two."""
    return [
        hint_item(key_hint("menu"), P_NORMAL),
        hint_item(key_hint("palette"), P_NORMAL - 1),
        hint_item(key_hint("help"), P_HIGH + 5),
    ]


class ContextLine(Static):
    """The bottom line. Feed it with :meth:`set_line` whenever the context changes."""

    DEFAULT_CSS = """
    ContextLine {
        height: 1; padding: 0 1; background: transparent; text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, markup=True)
        self._left: tuple[StatusItem, ...] = ()
        self._right: tuple[StatusItem, ...] = tuple(routes())

    def set_line(
        self,
        context: Sequence[StatusItem],
        message: StatusItem | None = None,
        routes_: Sequence[StatusItem] | None = None,
    ) -> None:
        """Show ``message`` if there is one, else ``context``; the routes on the right."""
        self._left = (StatusItem(message.markup, P_MESSAGE),) if message else tuple(context)
        if routes_ is not None:
            self._right = tuple(routes_)
        self._compose_line()

    def on_resize(self) -> None:
        self._compose_line()

    def _compose_line(self) -> None:
        width = self.content_size.width
        if not width:
            width = max(20, (self.app.size.width if self.is_attached else 80) - 2)
        self.update(fit_line(self._left, self._right, width))


def context_item(text: str, priority: int = P_NORMAL) -> StatusItem:
    """A plain context fact, such as the selected signal's value."""
    return StatusItem(escape(text), priority)
