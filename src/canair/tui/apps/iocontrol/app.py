"""Textual view for ``canair io ECU``: toggle an ECU's actuators from a table.

Each row is one IOControl DID. The rows update in place as replies arrive (and on every
``--poll`` pass), so the view never repaints the whole screen. The controller
(:class:`canair.live.actuate.iocontrol.IOControlController`) holds the state and its actuator
sends the requests; this app only turns keys into those calls and shows the result. The table,
the status bar and the one-request worker are :mod:`canair.tui.row_view`.

``q`` waits for the request in flight, and the command releases every actuator the session
switched on after the app has closed
(:func:`canair.live.actuate.iocontrol.run_iocontrol_view`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.widgets import Static

from canair.core.stop_signals import graceful_stop_async
from canair.live.actuate.iocontrol import IOControlController
from canair.live.actuate.iocontrol_render import (
    COLUMNS,
    IOCONTROL_LEGEND,
    row_cells,
    session_text,
)
from canair.tui.help import HelpMixin
from canair.tui.keys import bind, key_hint
from canair.tui.modals import TextPromptModal
from canair.tui.row_view import (
    ROW_VIEW_BINDINGS,
    ROW_VIEW_CSS,
    ROW_VIEW_HELP_ROWS,
    LiveTable,
    RowViewMixin,
    label_problem,
)
from canair.tui.status import P_ESSENTIAL, P_LOW, P_NORMAL, StatusBar, StatusItem, hint_item


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


class IOControlApp(RowViewMixin[IOControlController], HelpMixin, App):
    """One ECU's IOControl DIDs, with their last command, status and reply."""

    HELP_TITLE = "canair io — keyboard shortcuts"
    HELP_LEGEND = IOCONTROL_LEGEND
    HELP_EXTRA_ROWS = ROW_VIEW_HELP_ROWS

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = ROW_VIEW_CSS

    PRIORITY_ACTIONS: ClassVar[frozenset[str]] = frozenset({"toggle_actuator"})

    BINDINGS: ClassVar[list[BindingType]] = [
        *ROW_VIEW_BINDINGS,
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
        self.init_row_view()

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield LiveTable(COLUMNS, id="table")
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        self.start_row_view()

    # -- what the row view shows ---------------------------------------------
    def row_keys(self) -> Sequence[str]:
        return self.controller.dids

    def row_cells(self, key: str) -> Sequence[Text]:
        return row_cells(self.controller, key)

    def header_text(self) -> Text:
        return session_text(self.controller)

    def status_items(self) -> list[StatusItem]:
        items: list[StatusItem] = []
        did = self.current()
        last = self.controller.last_value.get(did) if did else None
        if last is not None:
            items.append(StatusItem(f"[dim]last value[/] {last.hex().upper()}", P_LOW))
        return [
            *items,
            hint_item(key_hint("accept", "toggle"), P_NORMAL),
            hint_item(key_hint("actuate_off"), P_NORMAL),
            hint_item(key_hint("set_value"), P_LOW),
            hint_item(key_hint("view"), P_LOW),
            hint_item(key_hint("help"), P_ESSENTIAL),
            hint_item(key_hint("quit"), P_ESSENTIAL),
        ]

    # -- actions -------------------------------------------------------------
    def _prompt_value(self, did: str, seed: str) -> None:
        def done(value: str | None) -> None:
            if value is None:
                return
            data = bytes.fromhex(value.replace(" ", ""))
            self.send(lambda: self.controller.actuator.send_adjust(did, data))

        title = f"Value for {did} (hex): sent as 2F {did} 03 <value>"
        self.push_screen(TextPromptModal(title, "01", seed, validate=hex_problem), done)

    def action_toggle_actuator(self) -> None:
        did = self.current()
        if not did:
            return

        def then(seed: object) -> None:
            if isinstance(seed, str):  # no simple ON: toggle asks for a value instead
                self._prompt_value(did, seed)

        self.send(lambda: self.controller.actuator.toggle(did), then)

    def action_off(self) -> None:
        did = self.current()
        if did:
            self.send(lambda: self.controller.actuator.send_off(did))

    def action_set_value(self) -> None:
        did = self.current()
        if not did:
            return
        refusal = self.controller.actuator.adjust_unsupported(did)
        if refusal:
            self.show(refusal, "error")
            return
        last = self.controller.last_value.get(did)
        self._prompt_value(did, last.hex().upper() if last else "")

    def action_step(self, delta: int) -> None:
        """Send the last value sent to this DID, plus or minus one."""
        did = self.current()
        if not did:
            return
        last = self.controller.last_value.get(did)
        if last is None:
            self.show(f"No value sent to {did} yet ({key_hint('set_value')})", "warning")
            return
        value = int.from_bytes(last, "big") + delta
        if not 0 <= value < 1 << (8 * len(last)):
            self.show(f"{did}: {last.hex().upper()} is at the end of its range", "warning")
            return
        data = value.to_bytes(len(last), "big")
        self.send(lambda: self.controller.actuator.send_adjust(did, data))

    def action_edit_label(self) -> None:
        did = self.current()
        if did:
            label = self.controller.all_cmds[did].get("label", "")
            self.prompt_edit(did, "label", f"Label for {did}", label, label_problem)

    def action_edit_notes(self) -> None:
        did = self.current()
        if did:
            notes = (self.controller.all_cmds[did].get("notes") or "").strip()
            self.prompt_edit(did, "notes", f"Notes for {did}", notes)

    def action_verify(self) -> None:
        did = self.current()
        if did:
            verified = bool(self.controller.all_cmds[did].get("verified", False))
            self.controller.apply_edit(did, "verified", not verified)
            self.refresh_view()

    def action_cycle_view(self) -> None:
        mode = self.controller.cycle_view()
        self.refresh_view()
        self.table().move_cursor(row=0)
        self.show(f"View: {mode}")

    def action_promote(self) -> None:
        did = self.current()
        if not did:
            return
        if not self.controller.all_cmds[did].get("discovery"):
            self.show(f"{did} is already curated ({key_hint('rename', 'edit label')})", "warning")
            return

        def done(label: str | None) -> None:
            if label is None:
                return
            self.controller.apply_promote(did, label)
            self.refresh_view()

        title = f"Label for the curated entry from {did} (on/off inferred from the reply)"
        self.push_screen(TextPromptModal(title, "", "", validate=label_problem), done)


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
