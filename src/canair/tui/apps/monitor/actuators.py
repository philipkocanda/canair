"""The monitor's actuator pane: the ``--actuate`` ECUs' outputs and routines, beside the values.

:class:`ActuatorPane` is a title line over an :class:`ActuatorTable`, one row per
:class:`~canair.live.monitor.actuation.Actuator`. The table owns the actuator keys (they act
while it has focus, :mod:`.panes`), and :class:`ActuatorMixin` gives the app their actions: it
reads the row under the cursor, asks for a value or a confirmation where one is needed, and puts
the request on the monitor's foreground lane, which sends it between two poll requests.

The pane is hidden until ``A`` shows it. It docks on the right when the terminal is at least
:data:`WIDE_COLUMNS` wide, else at the bottom. Docked, it is as wide as its table needs, between
:data:`MIN_SHARE` and :data:`MAX_SHARE` of the screen, so the last columns are not cut off. Hiding it releases nothing: the status line keeps
showing how many outputs are ON, and the monitor releases them when it exits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from canair.live.monitor.actuation import Actuator
from canair.live.monitor.actuator_rows import ACTUATOR_COLUMNS, actuator_cells, pane_title
from canair.live.monitor.controller import MonitorController
from canair.tui.flash import Severity
from canair.tui.keys import bind, key_hint
from canair.tui.modals import ConfirmModal, TextPromptModal
from canair.tui.row_view import LiveTable, hex_problem
from canair.uds.subfunctions import RoutineControlType

#: From this many columns on, the pane sits beside the values instead of below them.
WIDE_COLUMNS = 160

#: The docked pane's width as a share of the screen: at least this, and at most that.
MIN_SHARE = 0.4
MAX_SHARE = 0.6

#: What a key outside the pane says when the run may not actuate.
NO_ACTUATE = "Start the monitor with --actuate ECU to switch outputs from here"

PANE_CSS = """
#panes { height: 1fr; layout: vertical; }
#panes.wide { layout: horizontal; }
#actuators { display: none; height: 40%; min-height: 6; border-top: solid $accent; }
#panes.wide #actuators { height: 1fr; width: 40%; border-top: none; border-left: solid $accent; }
#actuators.shown { display: block; }
#actuators-title { height: 1; padding: 0 1; }
#actuator-table { height: 1fr; background: transparent; }
#actuator-table > .datatable--header { background: transparent; color: $text-muted; }
#actuator-table:focus > .datatable--cursor {
    background: ansi_bright_black; color: ansi_bright_white; text-style: bold;
}
"""


class ActuatorTable(LiveTable):
    """The actuator rows, and the keys that act on the row under the cursor."""

    HELP_SECTION: ClassVar[str] = "Actuators (with --actuate)"

    BINDINGS: ClassVar[list[BindingType]] = [
        # A subclass binding replaces the table's own enter (row selection).
        *bind("accept", "app.actuator_accept", desc="toggle"),
        *bind("actuate_off", "app.actuator_off"),
        *bind("set_value", "app.actuator_set_value"),
        *bind("increase", "app.actuator_step(1)"),
        *bind("decrease", "app.actuator_step(-1)"),
        *bind("start_routine", "app.actuator_start"),
        *bind("stop_routine", "app.actuator_stop"),
        *bind("move_down", "cursor_down", desc="down", show=False),
        *bind("move_up", "cursor_up", desc="up", show=False),
        *bind("axis_start", "scroll_top", desc="top", show=False),
        *bind("axis_end", "scroll_bottom", desc="bottom", show=False),
        *bind("back", "app.focus_values", desc="back"),
    ]


class ActuatorPane(Vertical):
    """A title line over the actuator table."""

    def compose(self) -> ComposeResult:
        yield Static("", id="actuators-title")
        yield ActuatorTable(ACTUATOR_COLUMNS, id="actuator-table")

    def table(self) -> ActuatorTable:
        return self.query_one(ActuatorTable)

    def sync(self, c: MonitorController) -> None:
        """Bring the title and every changed cell up to date."""
        a = c.actuation
        if a is None:
            return
        rows = {act.key: act for act in a.actuators()}
        self.query_one("#actuators-title", Static).update(pane_title(c))
        self.table().sync(list(rows), lambda key: actuator_cells(c, rows[key]))
        self.fit()

    def fit(self, total: int | None = None) -> None:
        """Docked beside the values, be as wide as the table needs (within the share limits).

        The columns only grow, so the width settles. Below the stacked layout's width the
        stylesheet decides, so the inline width is dropped. ``total`` is the screen width when
        the caller knows a newer one than the layout has.
        """
        parent = self.parent
        if parent is None or not parent.has_class("wide"):
            self.styles.width = None
            return
        total = total or self.app.size.width
        if total <= 0:
            return  # not laid out yet: the stylesheet's share holds until the next sync
        needed = self.table().virtual_size.width + 2  # the border and the scrollbar
        self.styles.width = max(int(total * MIN_SHARE), min(needed, int(total * MAX_SHARE)))


class ActuatorMixin:
    """The app's actuator actions: the pane's keys, ``A`` and the pane focus keys."""

    controller: MonitorController

    if TYPE_CHECKING:

        def _flash(
            self, msg: str, severity: Severity = "info", secs: float | None = None
        ) -> None: ...
        def _update_status(self) -> None: ...
        def _update_header(self) -> None: ...

    @property
    def _app(self) -> App:
        assert isinstance(self, App)
        return self

    # -- the pane --------------------------------------------------------------
    def _pane(self) -> ActuatorPane | None:
        try:
            return self._app.query_one("#actuators", ActuatorPane)
        except NoMatches:
            return None

    def actuators_shown(self) -> bool:
        pane = self._pane()
        return pane is not None and pane.has_class("shown")

    def actuators_focused(self) -> bool:
        # The app's own screen: while a menu or the palette is open, the table keeps the focus
        # it will get back.
        focused = self._app.screen_stack[0].focused
        return isinstance(focused, ActuatorTable)

    def refresh_actuators(self) -> None:
        """Repaint the pane and flash a warning or error a request left behind."""
        pane = self._pane()
        a = self.controller.actuation
        if pane is None or a is None:
            return
        if pane.has_class("shown"):
            pane.sync(self.controller)
        taken = a.take_status()
        if taken:
            self._flash(taken[0], taken[1])

    def layout_panes(self, width: int) -> None:
        """Side by side on a wide terminal, one above the other otherwise."""
        try:
            panes = self._app.query_one("#panes")
        except NoMatches:
            return
        panes.set_class(width >= WIDE_COLUMNS, "wide")
        pane = self._pane()
        if pane is not None:
            pane.fit(width)

    def action_toggle_actuators(self) -> None:
        pane = self._pane()
        if pane is None or self.controller.actuation is None:
            self._flash(NO_ACTUATE, "warning")
            return
        shown = not pane.has_class("shown")
        pane.set_class(shown, "shown")
        if shown:
            pane.sync(self.controller)
            pane.table().focus()
        else:
            self.action_focus_values()
        self._update_status()

    def action_focus_values(self) -> None:
        try:
            self._app.query_one("#scroll").focus()
        except NoMatches:
            return
        self._update_status()

    def action_focus_pane(self, delta: int) -> None:
        """Move focus to the next (``delta`` 1) or previous visible pane."""
        if not self.actuators_shown():
            return
        pane = self._pane()
        assert pane is not None
        if self.actuators_focused():
            self.action_focus_values()
        else:
            pane.table().focus()
            self._update_status()

    # -- the row under the cursor ------------------------------------------------
    def _current(self) -> Actuator | None:
        pane = self._pane()
        a = self.controller.actuation
        if pane is None or a is None or not self.actuators_focused():
            return None
        key = pane.table().current()
        return a.actuator(key) if key else None

    def _send(self, done: asyncio.Future) -> None:
        """Repaint now (the row shows queued) and again when the lane has run the request."""
        self.refresh_actuators()

        async def wait() -> None:
            try:
                await done
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._flash(f"Request failed: {exc}", "error")
            self.refresh_actuators()
            self._update_header()
            self._update_status()

        self._app.run_worker(wait(), group="actuation")

    def _io(self) -> Actuator | None:
        act = self._current()
        if act is not None and act.kind != "iocontrol":
            self._flash(f"{act.ident} is a routine: {key_hint('start_routine')}", "warning")
            return None
        return act

    def _routine(self) -> Actuator | None:
        act = self._current()
        if act is not None and act.kind != "routine":
            self._flash(f"{act.ident} is an output, not a routine", "warning")
            return None
        return act

    def _prompt_value(self, act: Actuator, seed: str) -> None:
        a = self.controller.actuation
        assert a is not None

        def done(value: str | None) -> None:
            if value is not None:
                self._send(a.submit_adjust(act, bytes.fromhex(value.replace(" ", ""))))

        title = f"Value for {act.ecu} {act.ident} (hex): sent as 2F {act.ident} 03 <value>"
        self._app.push_screen(TextPromptModal(title, "01", seed, validate=hex_problem), done)

    # -- the keys ------------------------------------------------------------------
    def action_actuator_accept(self) -> None:
        act = self._current()
        a = self.controller.actuation
        if act is None or a is None:
            return
        if act.kind == "routine":
            self._send(a.submit_routine(act, RoutineControlType.REQUEST_ROUTINE_RESULTS))
            return
        refusal = a.adjust_refusal(act) if not a.io_bank(act).all_cmds[act.ident]["on"] else None
        if refusal:
            self._flash(refusal, "error")
            return
        seed = a.value_seed(act)
        if seed is not None:  # no simple ON: it needs a value
            self._prompt_value(act, seed)
            return
        self._send(a.submit_toggle(act))

    def action_actuator_off(self) -> None:
        act = self._io()
        a = self.controller.actuation
        if act is not None and a is not None:
            self._send(a.submit_off(act))

    def action_actuator_set_value(self) -> None:
        act = self._io()
        a = self.controller.actuation
        if act is None or a is None:
            return
        refusal = a.adjust_refusal(act)
        if refusal:
            self._flash(refusal, "error")
            return
        last = a.io_bank(act).last_value.get(act.ident)
        self._prompt_value(act, last.hex().upper() if last else "")

    def action_actuator_step(self, delta: int) -> None:
        """Send the last value sent to this output, plus or minus one."""
        act = self._io()
        a = self.controller.actuation
        if act is None or a is None:
            return
        last = a.io_bank(act).last_value.get(act.ident)
        if last is None:
            self._flash(f"No value sent to {act.ident} yet ({key_hint('set_value')})", "warning")
            return
        value = int.from_bytes(last, "big") + delta
        if not 0 <= value < 1 << (8 * len(last)):
            self._flash(f"{act.ident}: {last.hex().upper()} is at the end of its range", "warning")
            return
        self._send(a.submit_adjust(act, value.to_bytes(len(last), "big")))

    def action_actuator_start(self) -> None:
        act = self._routine()
        a = self.controller.actuation
        if act is None or a is None:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self._send(a.submit_routine(act, RoutineControlType.START_ROUTINE))
            else:
                self._flash("startRoutine cancelled")

        self._app.push_screen(
            ConfirmModal(
                f"Start routine {act.ecu} {act.ident} ({act.label})?",
                "startRoutine (31 01) may actuate hardware. Go on only if you know its effect.",
                confirm_label="Start",
            ),
            done,
        )

    def action_actuator_stop(self) -> None:
        act = self._routine()
        a = self.controller.actuation
        if act is not None and a is not None:
            self._send(a.submit_routine(act, RoutineControlType.STOP_ROUTINE))
