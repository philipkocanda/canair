"""Textual view for ``canair io ECU``: toggle an ECU's actuators from a table.

Each row is one IOControl DID. The rows update in place as replies arrive (and on every
``--poll`` pass), so the view never repaints the whole screen. The controller
(:class:`canair.live.actuate.iocontrol.IOControlController`) holds the state and its actuator
sends the requests; this app only turns keys into those calls and shows the result.

One request is in flight at a time. ``q`` waits for it to finish, and the command releases
every actuator the session switched on after the app has closed
(:func:`canair.live.actuate.iocontrol.run_iocontrol_view`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from canair.core.stop_signals import graceful_stop_async
from canair.live.actuate.iocontrol import IOControlController
from canair.live.actuate.iocontrol_render import (
    COLUMNS,
    IOCONTROL_LEGEND,
    row_cells,
    session_text,
)
from canair.render.glyphs import ELLIPSIS
from canair.tui.flash import Flash, Severity
from canair.tui.help import HelpMixin
from canair.tui.keys import bind, display_key, key_hint
from canair.tui.modals import TextPromptModal
from canair.tui.status import P_ESSENTIAL, P_LOW, P_NORMAL, StatusBar, StatusItem, hint_item

BUSY = f"A request is in flight{ELLIPSIS} try again when it is answered"


def hex_problem(value: str) -> str | None:
    """Why ``value`` is not a ShortTermAdjustment value (hex bytes), or None."""
    digits = value.replace(" ", "")
    if not digits:
        return "Type at least one byte, such as 01"
    try:
        bytes.fromhex(digits)
    except ValueError:
        return "Type hex bytes, such as 01 or 0A0B"
    return None


def _label_problem(value: str) -> str | None:
    return None if value else "A label cannot be empty"


class IOControlApp(HelpMixin, App):
    """One ECU's IOControl DIDs, with their last command, status and reply."""

    HELP_TITLE = "canair io — keyboard shortcuts"
    HELP_LEGEND = IOCONTROL_LEGEND
    HELP_EXTRA_ROWS: ClassVar[tuple[tuple[str, str], ...]] = (
        (f"{display_key('pageup')}/{display_key('pagedown')}", "page up / down"),
    )

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #header { height: 1; padding: 0 1; background: transparent; }
    #table {
        height: 1fr; background: transparent; scrollbar-size-vertical: 1; margin: 1 0 0 0;
    }
    #table > .datatable--header, #table > .datatable--header-hover {
        background: transparent; color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
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
        # priority: the focused table would take enter for its own row selection.
        *bind("accept", "toggle_actuator", desc="toggle", priority=True),
        *bind("pause", "toggle_actuator", desc="toggle"),
        *bind("actuate_off", "off"),
        *bind("set_value", "set_value"),
        *bind("increase", "step(1)"),
        *bind("decrease", "step(-1)"),
        *bind("rename", "edit_label", desc="edit label"),
        *bind("edit", "edit_notes", desc="edit note"),
        *bind("verify", "verify"),
        *bind("view", "cycle_view"),
        *bind("promote", "promote"),
    ]

    def __init__(self, controller: IOControlController):
        super().__init__()
        self.controller = controller
        self.flash = Flash()
        self._inflight = False
        self._rows: list[str] = []  # the DIDs in the table, in row order
        self._shown: dict[str, tuple[Text, ...]] = {}  # the cells on screen, per DID

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield DataTable(
            id="table",
            cursor_type="row",
            cursor_foreground_priority="renderable",
        )
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        table = self._table()
        for key, heading in COLUMNS:
            table.add_column(heading, key=key)
        self._rebuild()
        table.focus()
        self.set_interval(0.25, self._refresh)
        self._refresh()

    def _table(self) -> DataTable:
        return self.query_one("#table", DataTable)

    # -- drawing -----------------------------------------------------------
    def _rebuild(self) -> None:
        """Re-fill the table for the controller's DIDs, keeping the cursor on its DID."""
        table = self._table()
        current = self._did()
        table.clear()
        self._rows = list(self.controller.dids)
        self._shown = {}
        for did in self._rows:
            cells = row_cells(self.controller, did)
            self._shown[did] = cells
            table.add_row(*cells, key=did)
        if current in self._rows:
            table.move_cursor(row=self._rows.index(current))

    def _refresh(self) -> None:
        """Bring the header, the changed cells and the status bar up to date."""
        try:
            header = self.query_one("#header", Static)
            table = self._table()
        except NoMatches:  # the timer can fire during teardown
            return
        c = self.controller
        if self._rows != c.dids:
            self._rebuild()
        for did in self._rows:
            cells = row_cells(c, did)
            old = self._shown.get(did)
            if cells == old:
                continue
            for (key, _heading), new, prev in zip(COLUMNS, cells, old or (), strict=False):
                if new != prev:
                    table.update_cell(did, key, new, update_width=True)
            self._shown[did] = cells
        header.update(session_text(c))
        if c.status and c.status_level != "info":
            taken = c.take_status()
            if taken:
                self._flash(*taken, refresh=False)
        self._update_status()

    def _update_status(self) -> None:
        try:
            bar = self.query_one("#status", StatusBar)
        except NoMatches:
            return
        c = self.controller
        items: list[StatusItem | None] = [self.flash.item()]
        if c.status:  # "Sending ON …", shown while the request is in flight
            items.append(StatusItem(c.status, P_ESSENTIAL))
        did = self._did()
        if did and did in c.last_value:
            items.append(StatusItem(f"[dim]last value[/] {c.last_value[did].hex().upper()}", P_LOW))
        items += [
            hint_item(key_hint("accept", "toggle"), P_NORMAL),
            hint_item(key_hint("actuate_off"), P_NORMAL),
            hint_item(key_hint("set_value"), P_LOW),
            hint_item(key_hint("view"), P_LOW),
            hint_item(key_hint("help"), P_ESSENTIAL),
            hint_item(key_hint("quit"), P_ESSENTIAL),
        ]
        bar.set_lines([i for i in items if i is not None])

    def _flash(self, msg: str, severity: Severity = "info", *, refresh: bool = True) -> None:
        self.flash.show(msg, severity, secs=5.0)
        if refresh:
            self._update_status()

    # -- helpers -----------------------------------------------------------
    def _did(self) -> str | None:
        """The DID under the cursor."""
        try:
            row = self._table().cursor_row
        except NoMatches:
            return None
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _modal_active(self) -> bool:
        return len(self.screen_stack) > 1

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # toggle_actuator is a priority binding, which runs before a dialog's own input; it
        # must stand down while a dialog is up, or enter could never submit one.
        if action == "toggle_actuator" and self._modal_active():
            return False
        return True

    def _send(self, make: Callable[[], Awaitable[object]]) -> None:
        """Run one request as a worker, unless one is already in flight."""
        if self._inflight or self.controller.busy:
            self._flash(BUSY, "warning")
            return
        self._inflight = True

        async def run() -> None:
            try:
                result = await make()
                if isinstance(result, str):  # toggle asks for a value instead
                    did = self._did()
                    if did:
                        self._prompt_value(did, result)
            finally:
                self._inflight = False
                self._refresh()
                if self.controller.stopping:
                    self.exit()

        self.run_worker(run(), name="actuate", group="actuate")

    def _prompt_value(self, did: str, seed: str) -> None:
        def done(value: str | None) -> None:
            if value is None:
                return
            data = bytes.fromhex(value.replace(" ", ""))
            self._send(lambda: self.controller.actuator.send_adjust(did, data))

        title = f"Value for {did} (hex): sent as 2F {did} 03 <value>"
        self.push_screen(
            TextPromptModal(title, "01", seed, validate=hex_problem),
            done,
        )

    # -- actions -----------------------------------------------------------
    async def action_quit(self) -> None:
        """Quit, after the request in flight (if any) has been answered."""
        self.controller.stopping = True
        if self._inflight:
            self._flash(f"Quitting when the request in flight is answered{ELLIPSIS}")
            return
        self.exit()

    def action_stay(self) -> None:
        """Escape: the table has no level to back out of, so it does nothing."""

    def action_cursor(self, delta: int) -> None:
        table = self._table()
        table.move_cursor(row=max(0, table.cursor_row + delta))

    def action_to_top(self) -> None:
        self._table().move_cursor(row=0)

    def action_to_bottom(self) -> None:
        self._table().move_cursor(row=max(0, len(self._rows) - 1))

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._update_status()

    def action_toggle_actuator(self) -> None:
        did = self._did()
        if did:
            self._send(lambda: self.controller.actuator.toggle(did))

    def action_off(self) -> None:
        did = self._did()
        if did:
            self._send(lambda: self.controller.actuator.send_off(did))

    def action_set_value(self) -> None:
        did = self._did()
        if not did:
            return
        refusal = self.controller.actuator.adjust_unsupported(did)
        if refusal:
            self._flash(refusal, "error")
            return
        last = self.controller.last_value.get(did)
        self._prompt_value(did, last.hex().upper() if last else "")

    def action_step(self, delta: int) -> None:
        """Send the last value sent to this DID, plus or minus one."""
        did = self._did()
        if not did:
            return
        last = self.controller.last_value.get(did)
        if last is None:
            self._flash(f"No value sent to {did} yet ({key_hint('set_value')})", "warning")
            return
        value = int.from_bytes(last, "big") + delta
        if not 0 <= value < 1 << (8 * len(last)):
            self._flash(f"{did}: {last.hex().upper()} is at the end of its range", "warning")
            return
        data = value.to_bytes(len(last), "big")
        self._send(lambda: self.controller.actuator.send_adjust(did, data))

    def _edit(self, field: str, title: str, value: str, validate=None) -> None:
        did = self._did()
        if not did:
            return

        def done(result: str | None) -> None:
            if result is None:
                return
            self.controller.apply_edit(did, field, result)
            self._refresh()

        self.push_screen(TextPromptModal(title, "", value, validate=validate), done)

    def action_edit_label(self) -> None:
        did = self._did()
        if did:
            label = self.controller.all_cmds[did].get("label", "")
            self._edit("label", f"Label for {did}", label, _label_problem)

    def action_edit_notes(self) -> None:
        did = self._did()
        if did:
            notes = (self.controller.all_cmds[did].get("notes") or "").strip()
            self._edit("notes", f"Notes for {did}", notes)

    def action_verify(self) -> None:
        did = self._did()
        if did:
            verified = bool(self.controller.all_cmds[did].get("verified", False))
            self.controller.apply_edit(did, "verified", not verified)
            self._refresh()

    def action_cycle_view(self) -> None:
        mode = self.controller.cycle_view()
        self._rebuild()
        self._table().move_cursor(row=0)
        self._flash(f"View: {mode}")
        self._refresh()

    def action_promote(self) -> None:
        did = self._did()
        if not did:
            return
        if not self.controller.all_cmds[did].get("discovery"):
            self._flash(f"{did} is already curated ({key_hint('rename', 'edit label')})", "warning")
            return

        def done(label: str | None) -> None:
            if label is None:
                return
            self.controller.apply_promote(did, label)
            self._refresh()

        title = f"Label for the curated entry from {did} (on/off inferred from the reply)"
        self.push_screen(TextPromptModal(title, "", "", validate=_label_problem), done)


async def run_iocontrol_app(controller: IOControlController) -> None:
    """Run the IOControl view until the user quits.

    A stop signal (``SIGTERM``/``SIGHUP``) goes through the app's own quit, so the terminal is
    restored and the actuators are released like after ``q``.
    """
    app = IOControlApp(controller)

    def _request_quit() -> None:
        app.run_worker(app.action_quit(), name="stop")

    with graceful_stop_async(_request_quit):
        await app.run_async()
