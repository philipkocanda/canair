"""Textual view for ``canair routines ECU``: query, stop and start an ECU's routines.

Each row is one Routine Identifier (RID). ``enter`` sends requestRoutineResults (``31 03``),
which only reads. ``S`` sends stopRoutine (``31 02``). ``!`` sends startRoutine (``31 01``),
which may actuate hardware, so it asks first in a confirmation dialog that starts on Cancel.
The table, the status bar and the one-request worker are :mod:`canair.tui.row_view`; the
state and the requests are :class:`canair.live.actuate.routines.RoutinesController`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.widgets import Static

from canair.core.stop_signals import graceful_stop_async
from canair.live.actuate.routines import (
    COLUMNS,
    ROUTINES_LEGEND,
    RoutinesController,
    routine_cells,
    routines_header,
)
from canair.tui.help import HelpMixin
from canair.tui.keys import bind, key_hint
from canair.tui.modals import ConfirmModal
from canair.tui.row_view import (
    ROW_VIEW_BINDINGS,
    ROW_VIEW_CSS,
    ROW_VIEW_HELP_ROWS,
    LiveTable,
    RowViewMixin,
    label_problem,
)
from canair.tui.status import P_ESSENTIAL, P_LOW, P_NORMAL, StatusBar, StatusItem, hint_item
from canair.uds.subfunctions import RoutineControlType


class RoutinesApp(RowViewMixin[RoutinesController], HelpMixin, App):
    """One ECU's routines, with the result of the last request to each."""

    HELP_TITLE = "canair routines — keyboard shortcuts"
    HELP_LEGEND = ROUTINES_LEGEND
    HELP_EXTRA_ROWS = ROW_VIEW_HELP_ROWS

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = ROW_VIEW_CSS

    PRIORITY_ACTIONS: ClassVar[frozenset[str]] = frozenset({"results"})

    BINDINGS: ClassVar[list[BindingType]] = [
        *ROW_VIEW_BINDINGS,
        # priority: the focused table would take enter for its own row selection.
        *bind("accept", "results", desc="request results", priority=True),
        *bind("stop_routine", "stop"),
        *bind("start_routine", "start"),
        *bind("rename", "edit_label", desc="edit label"),
        *bind("edit", "edit_notes", desc="edit note"),
        *bind("verify", "verify"),
    ]

    def __init__(self, controller: RoutinesController):
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
        return self.controller.rids

    def row_cells(self, key: str) -> Sequence[Text]:
        return routine_cells(self.controller, key)

    def header_text(self) -> Text:
        return routines_header(self.controller)

    def status_items(self) -> list[StatusItem]:
        return [
            hint_item(key_hint("accept", "request results"), P_NORMAL),
            hint_item(key_hint("stop_routine"), P_LOW),
            hint_item(key_hint("start_routine"), P_LOW),
            hint_item(key_hint("help"), P_ESSENTIAL),
            hint_item(key_hint("quit"), P_ESSENTIAL),
        ]

    # -- actions -------------------------------------------------------------
    def _send_sf(self, sub_function: int) -> None:
        rid = self.current()
        if rid:
            self.send(lambda: self.controller.send(rid, sub_function))

    def action_results(self) -> None:
        self._send_sf(RoutineControlType.REQUEST_ROUTINE_RESULTS)

    def action_stop(self) -> None:
        self._send_sf(RoutineControlType.STOP_ROUTINE)

    def action_start(self) -> None:
        rid = self.current()
        if not rid:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.send(lambda: self.controller.send(rid, RoutineControlType.START_ROUTINE))
            else:
                self.show("startRoutine cancelled")

        label = self.controller.routines[rid].get("label") or rid
        self.push_screen(
            ConfirmModal(
                f"Start routine {rid} ({label})?",
                "startRoutine (31 01) may actuate hardware. Go on only if you know its effect.",
                confirm_label="Start",
            ),
            done,
        )

    def action_edit_label(self) -> None:
        rid = self.current()
        if rid:
            label = self.controller.routines[rid].get("label") or ""
            self.prompt_edit(rid, "label", f"Label for {rid}", label, label_problem)

    def action_edit_notes(self) -> None:
        rid = self.current()
        if rid:
            notes = (self.controller.routines[rid].get("notes") or "").strip()
            self.prompt_edit(rid, "notes", f"Notes for {rid}", notes)

    def action_verify(self) -> None:
        rid = self.current()
        if rid:
            verified = bool(self.controller.routines[rid].get("verified", False))
            self.controller.apply_edit(rid, "verified", not verified)
            self.refresh_view()


async def run_routines_app(controller: RoutinesController) -> None:
    """Run the routines view until the user quits.

    A stop signal (``SIGTERM``/``SIGHUP``) goes through the app's own quit, so the terminal is
    restored and the session is closed like after ``q``.
    """
    app = RoutinesApp(controller)

    def _request_quit() -> None:
        app.run_worker(app.action_quit(), name="stop")

    with graceful_stop_async(_request_quit):
        await app.run_async()
