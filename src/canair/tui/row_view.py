"""The shared shape of the ``io`` and ``routines`` views: a live table of identifiers.

Both views show one row per identifier (a DID, a RID), send one request at a time for the row
under the cursor, and change a few cells whenever a reply arrives.

- :class:`LiveTable` re-fills itself only when the set of rows changes (a view mode, a reload)
  and otherwise updates just the cells whose text changed, so a repaint never moves the cursor
  or the scroll position.
- :class:`RowViewMixin` is the app around it: the header, the status bar, the one-request
  worker, and a ``q`` that waits for the request in flight. An app mixes it in before
  ``HelpMixin`` and ``App``, starts its ``BINDINGS`` with :data:`ROW_VIEW_BINDINGS`, and
  supplies the rows, the header and its hints.

The controller the app drives keeps its state in plain attributes: ``busy`` while a request is
on the bus, ``stopping`` once the user quits, and a ``status``/``status_level`` message that
:meth:`take_status` hands over once.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar, Protocol, cast

from rich.markup import escape
from rich.style import Style
from rich.text import Span, Text
from textual.app import App
from textual.binding import BindingType
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from canair.render.glyphs import ELLIPSIS
from canair.tui.flash import Flash, Severity
from canair.tui.keys import bind, display_key
from canair.tui.modals import TextPromptModal
from canair.tui.status import P_ESSENTIAL, StatusBar, StatusItem

CellsFor = Callable[[str], Sequence[Text]]

BUSY = f"A request is in flight{ELLIPSIS} try again when it is answered"

#: The keys every row view shares; an app's ``BINDINGS`` starts with these.
ROW_VIEW_BINDINGS: list[BindingType] = [
    *bind("quit", "quit"),
    *bind("quit_force", "quit", show=False, priority=True),
    *bind("help", "help"),
    # The top level has nothing to back out of: escape stays inert rather than quitting.
    *bind("back", "stay", show=False),
    # The arrows are the table's own; j/k reach it through these.
    *bind("move_down", "cursor(1)", desc="select down", show=False),
    *bind("move_up", "cursor(-1)", desc="select up", show=False),
    *bind("axis_start", "to_top", desc="top", show=False),
    *bind("axis_end", "to_bottom", desc="bottom", show=False),
]

#: The table's paging keys, which it handles itself.
ROW_VIEW_HELP_ROWS: tuple[tuple[str, str], ...] = (
    (f"{display_key('pageup')}/{display_key('pagedown')}", "page up / down"),
)

ROW_VIEW_CSS = """
Screen { layout: vertical; background: transparent; }
#header { height: 1; padding: 0 1; background: transparent; }
#table { margin: 1 0 0 0; }
#table > .datatable--header, #table > .datatable--header-hover {
    background: transparent; color: $text-muted;
}
/* A dark cursor bar keeps the cells' own colours readable; LiveTable un-dims its row. */
#table:focus > .datatable--cursor {
    background: ansi_bright_black; color: ansi_bright_white; text-style: bold;
}
"""


class RowController(Protocol):
    """What the mixin reads off a view's controller."""

    busy: bool
    stopping: bool
    status: str
    status_level: Any

    def take_status(self) -> tuple[str, Any] | None: ...

    def apply_edit(self, key: str, field: str, value: str | bool, /) -> None: ...


def label_problem(value: str) -> str | None:
    """Why ``value`` cannot be a label, or None."""
    return None if value else "A label cannot be empty"


def undimmed(text: Text) -> Text:
    """``text`` with its dim spans at full brightness, for the row under the cursor."""

    def bright(style: str | Style) -> Style:
        return (Style.parse(style) if isinstance(style, str) else style) + Style(dim=False)

    out = Text(text.plain, style=bright(text.style), end=text.end)
    out.spans = [Span(span.start, span.end, bright(span.style)) for span in text.spans]
    return out


def _same(a: Text, b: Text) -> bool:
    # Text's own == ignores the base style, which is how most cells are coloured.
    return a == b and str(a.style) == str(b.style)


class LiveTable(DataTable):
    """A row-cursor ``DataTable`` keyed by identifier, synced from a cells function."""

    DEFAULT_CSS = """
    LiveTable { height: 1fr; background: transparent; scrollbar-size-vertical: 1; }
    """

    def __init__(self, columns: Sequence[tuple[str, str]], *, id: str | None = None) -> None:
        # The cell colours carry meaning, so the cursor row keeps them.
        super().__init__(id=id, cursor_type="row", cursor_foreground_priority="renderable")
        self._columns = tuple(columns)
        self.keys: list[str] = []
        self._shown: dict[str, tuple[Text, ...]] = {}
        self._cells_for: CellsFor | None = None
        for key, heading in self._columns:
            self.add_column(heading, key=key)

    def current(self) -> str | None:
        """The key of the row under the cursor."""
        row = self.cursor_row
        return self.keys[row] if 0 <= row < len(self.keys) else None

    def _display(self, key: str, cells: Sequence[Text]) -> tuple[Text, ...]:
        """The cells as drawn: the row under the cursor has no dim text on its dark bar."""
        if key == self.current():
            return tuple(undimmed(c) for c in cells)
        return tuple(cells)

    def sync(self, keys: Sequence[str], cells_for: CellsFor) -> None:
        """Show one row per key, updating only the cells that changed."""
        self._cells_for = cells_for
        if list(keys) != self.keys:
            self._refill(keys, cells_for)
            return
        for key in self.keys:
            self._update_row(key, cells_for(key))

    def _update_row(self, key: str, cells: Sequence[Text]) -> None:
        drawn = self._display(key, cells)
        old = self._shown.get(key, ())
        changed = False
        for (column, _heading), new, prev in zip(self._columns, drawn, old, strict=False):
            if not _same(new, prev):
                self.update_cell(key, column, new, update_width=True)
                changed = True
        if changed:
            self._shown[key] = drawn

    def _refill(self, keys: Sequence[str], cells_for: CellsFor) -> None:
        current = self.current()
        self.clear()
        self.keys = list(keys)
        self._shown = {}
        for key in self.keys:
            cells = tuple(cells_for(key))
            self._shown[key] = cells
            self.add_row(*cells, key=key)
        if current in self.keys:
            self.move_cursor(row=self.keys.index(current))
        self.redraw_cursor_rows()

    def redraw_cursor_rows(self) -> None:
        """Re-draw after the cursor moved: the rows it left and reached change brightness."""
        if self._cells_for is not None:
            for key in self.keys:
                self._update_row(key, self._cells_for(key))


class RowViewMixin[C: RowController]:
    """The header, status bar, request worker and quit of a live row view."""

    controller: C
    #: Actions bound with ``priority`` (they run before a dialog's own keys) that must stand
    #: down while a dialog is up.
    PRIORITY_ACTIONS: ClassVar[frozenset[str]] = frozenset()

    def init_row_view(self) -> None:
        self.flash = Flash()
        self._inflight = False

    # -- what the app supplies -----------------------------------------------
    def row_keys(self) -> Sequence[str]:
        raise NotImplementedError

    def row_cells(self, key: str) -> Sequence[Text]:
        raise NotImplementedError

    def header_text(self) -> Text:
        raise NotImplementedError

    def status_items(self) -> list[StatusItem]:
        """The app's own status-bar items: hints and per-row detail."""
        return []

    # -- the shared behaviour ------------------------------------------------
    def _app(self) -> App:
        return cast(App, self)

    def start_row_view(self) -> None:
        """Call from ``on_mount``: fill the table, focus it and start the refresh timer."""
        app = self._app()
        if "ansi-dark" in app.available_themes:
            app.theme = "ansi-dark"
        self.table().focus()
        app.set_interval(0.25, self.refresh_view)
        self.refresh_view()

    def table(self) -> LiveTable:
        return self._app().query_one("#table", LiveTable)

    def current(self) -> str | None:
        """The key of the row under the cursor."""
        try:
            return self.table().current()
        except NoMatches:
            return None

    def refresh_view(self) -> None:
        """Bring the header, the changed cells and the status bar up to date."""
        app = self._app()
        try:
            header = app.query_one("#header", Static)
            table = self.table()
        except NoMatches:  # the timer can fire during teardown
            return
        table.sync(self.row_keys(), self.row_cells)
        header.update(self.header_text())
        c = self.controller
        # An in-flight "Sending …" is shown while it lasts; any other message is kept.
        if c.status and c.status_level != "info":
            taken = c.take_status()
            if taken:
                self.flash.show(taken[0], taken[1], secs=5.0)
        self.update_status()

    def update_status(self) -> None:
        try:
            bar = self._app().query_one("#status", StatusBar)
        except NoMatches:
            return
        items: list[StatusItem] = []
        flash = self.flash.item()
        if flash:
            items.append(flash)
        if self.controller.status:
            items.append(StatusItem(escape(self.controller.status), P_ESSENTIAL))
        bar.set_lines(items + self.status_items())

    def show(self, msg: str, severity: Severity = "info") -> None:
        """Flash ``msg`` in the status bar for a few seconds."""
        self.flash.show(msg, severity, secs=5.0)
        self.update_status()

    def modal_active(self) -> bool:
        return len(self._app().screen_stack) > 1

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self.PRIORITY_ACTIONS and self.modal_active():
            return False
        return True

    def send(
        self,
        make: Callable[[], Awaitable[object]],
        then: Callable[[object], None] | None = None,
    ) -> None:
        """Run one request as a worker, unless one is in flight; ``then`` gets its result."""
        if self._inflight or self.controller.busy:
            self.show(BUSY, "warning")
            return
        self._inflight = True

        async def run() -> None:
            try:
                result = await make()
                if then is not None:
                    then(result)
            finally:
                self._inflight = False
                self.refresh_view()
                if self.controller.stopping:
                    self._app().exit()

        self._app().run_worker(run(), name="request", group="request")

    def prompt_edit(
        self,
        key: str,
        field: str,
        title: str,
        value: str,
        validate: Callable[[str], str | None] | None = None,
    ) -> None:
        """Ask for a new ``field`` of ``key`` and write it through the controller."""

        def done(result: str | None) -> None:
            if result is None:
                return
            self.controller.apply_edit(key, field, result)
            self.refresh_view()

        self._app().push_screen(TextPromptModal(title, "", value, validate=validate), done)

    # -- actions -------------------------------------------------------------
    async def action_quit(self) -> None:
        """Quit, after the request in flight (if any) has been answered."""
        self.controller.stopping = True
        if self._inflight:
            self.show(f"Quitting when the request in flight is answered{ELLIPSIS}")
            return
        self._app().exit()

    def action_stay(self) -> None:
        """Escape: the table has no level to back out of, so it does nothing."""

    def action_cursor(self, delta: int) -> None:
        table = self.table()
        table.move_cursor(row=max(0, table.cursor_row + delta))

    def action_to_top(self) -> None:
        self.table().move_cursor(row=0)

    def action_to_bottom(self) -> None:
        self.table().move_cursor(row=max(0, len(self.table().keys) - 1))

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        try:
            self.table().redraw_cursor_rows()
        except NoMatches:
            return
        self.update_status()
