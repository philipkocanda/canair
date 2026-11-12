"""Textual TUI for the live monitor (``canair monitor``), on the application shell.

Three rows of chrome around the values, built from :mod:`canair.tui.shell`:

* the **menu bar** (``f10``, ``alt+letter`` or a click): every command, with its key and its
  state, from the catalogue in :mod:`.commands`; the recording's label on the right;
* the **status line**: the link, poll, recording and car zones of :mod:`.status`, each a click
  away from its detail (the event log, the Poll menu, recording info, the session picker);
* the **bottom line**: a message, or the selection, the keys of the focused pane and the modes
  that are not the default; ``f10 menu · ctrl+p commands · ? help`` on its right.

``ctrl+p`` searches every command. ``?`` is the full key list. A message replaces the context
for a few seconds; an error stays until the next key, and every message is kept in the event log.

The values render into a single widget that updates *in place* inside a scrollable container,
fitted to the pane's width. A data refresh **never** moves the scroll position, so a byte you are
reading cannot be yanked out from under you; ``G``/``End`` jumps to the newest output.

The monitor doubles as a light PID editor: the arrows move a cursor over the signal rows, ``e``
opens the edit dialog, ``v`` and ``x`` toggle verified and ship, ``F`` cycles the filter. Edits go
through :mod:`canair.yaml_edit.pids` and show on the next poll. Those keys belong to the values
pane (:class:`~canair.tui.apps.monitor.panes.ValuesPane`); the app holds the keys that mean the
same wherever focus is.

With ``--save`` the recording is journaled as it goes and written on exit; ``s`` labels it and
``n`` finishes it and starts the next. Without ``--save`` nothing records until ``n`` starts a
recording, and until then ``s`` writes a one-off snapshot. With
``--actuate``, ``A`` shows the actuator pane (:mod:`.actuators`). ``p`` swaps what is polled
without a restart; ``t`` opens a diagnostic session type on one ECU.

The dialogs are :mod:`.dialogs`; the overlays and the session-type picker :mod:`.overlays`; the
poll loop, the save keys and the signal editing the mixins of :mod:`.polling`, :mod:`.recording`
and :mod:`.editing`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from canair.core.stop_signals import graceful_stop_async
from canair.live.monitor.render import MONITOR_LEGEND
from canair.render.glyphs import ELLIPSIS
from canair.tui.apps.monitor.actuators import (
    NO_ACTUATE,
    PANE_CSS,
    ActuatorMixin,
    ActuatorPane,
    ActuatorTable,
)
from canair.tui.apps.monitor.commands import (
    ACTUATOR_COMMANDS,
    SIGNAL_COMMANDS,
    monitor_catalogue,
)
from canair.tui.apps.monitor.dialogs import EditParamDialog, SaveDialog
from canair.tui.apps.monitor.editing import EditingMixin
from canair.tui.apps.monitor.overlays import EventLogModal, SessionInfoModal, SessionPickerScreen
from canair.tui.apps.monitor.panes import ValuesPane
from canair.tui.apps.monitor.polling import PollingMixin
from canair.tui.apps.monitor.recording import RecordingMixin
from canair.tui.apps.monitor.status import (
    SELECT_FIRST,
    context_items,
    document_items,
    status_zones,
)
from canair.tui.apps.monitor_select.app import LiveMonitorSelectScreen
from canair.tui.flash import Severity, severity_of
from canair.tui.help import HelpMixin
from canair.tui.keys import bind, key_hint
from canair.tui.scroll import APP_BASE_CSS
from canair.tui.shell.app import SHELL_BINDINGS, ShellMixin
from canair.tui.shell.commands import Catalogue, Command
from canair.tui.shell.contextline import ContextLine
from canair.tui.shell.menubar import MenuBar
from canair.tui.shell.messages import Messages
from canair.tui.shell.palette import ShellCommands
from canair.tui.shell.statusline import StatusLine

if TYPE_CHECKING:
    from canair.live.monitor.controller import MonitorController

__all__ = [
    "EditParamDialog",
    "EventLogModal",
    "MonitorApp",
    "SaveDialog",
    "SessionInfoModal",
    "SessionPickerScreen",
]

#: The status zone a click opens, by zone name (the poll zone opens its menu instead).
ZONE_ACTIONS = {"link": "event_log", "record": "session_info", "car": "session_type"}


class MonitorApp(
    ShellMixin, PollingMixin, RecordingMixin, EditingMixin, ActuatorMixin, HelpMixin, App
):
    """Scrollable, in-place live-value monitor."""

    HELP_TITLE = "canair monitor — keyboard shortcuts"
    HELP_LEGEND = MONITOR_LEGEND
    HELP_PANES = (ValuesPane, ActuatorTable)

    # The palette lists the monitor's own commands (tui/shell/palette.py), nothing of Textual's.
    ENABLE_COMMAND_PALETTE = True
    COMMANDS: ClassVar = {ShellCommands}

    CSS = APP_BASE_CSS + PANE_CSS

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("help", "help"),
        *SHELL_BINDINGS,
        *bind("session", "save", desc="save / label"),
        *bind("new", "new_segment", desc="new recording"),
        *bind("rulers", "toggle_rulers", desc="byte ruler"),
        *bind("event_log", "event_log"),
        *bind("info", "session_info", desc="recording info"),
        *bind("view", "cycle_view"),
        *bind("pause", "toggle_pause"),
        *bind("increase", "faster", desc="poll faster"),
        *bind("decrease", "slower", desc="poll slower"),
        *bind("mode", "toggle_schedule", desc="poll schedule"),
        # The values' own keys (the signal cursor, edit, verify, …) are the ValuesPane's.
        # At the top level escape has nothing to back out of.
        *bind("back", "stay", desc="back", show=False),
        *bind("reconnect", "force_reconnect"),
        *bind("pick", "pid_picker", desc="select ECUs/PIDs"),
        *bind("value_type", "session_type", desc="diagnostic session"),
        *bind("actuators", "toggle_actuators"),
        *bind("block_next", "focus_pane(1)", desc="next pane", show=False),
        *bind("block_prev", "focus_pane(-1)", desc="prev pane", show=False),
    ]

    def __init__(self, controller: MonitorController):
        super().__init__()
        self.controller = controller
        self.paused = False
        # Set when the user asks to quit, so an in-flight reconnect aborts cleanly.
        self._stopping = False
        self.shell_messages = Messages()
        self._catalogues: dict[bool, Catalogue] = {}

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield MenuBar(self.shell_catalogue().menus, self.SHELL_NAME, id="menubar")
        with Container(id="panes"):
            with ValuesPane(id="scroll"):
                yield Static("", id="body", markup=False)
            if self.controller.actuation is not None:
                yield ActuatorPane(id="actuators")
        yield StatusLine(id="status")
        yield ContextLine(id="context")

    def on_mount(self) -> None:
        # Use the terminal's own palette + default background (ansi_default) rather than
        # Textual's remapped truecolor theme + grey surface. Keeps byte colours matching the
        # plain output and readable on any themed terminal (iTerm2, Termius, …).
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        # Let the controller repaint mid-cycle as each PID resolves, so a slow / timing-out
        # PID never freezes the whole view (only its own row lags).
        self.controller.on_partial = self._refresh_body
        self.run_worker(self._poll_loop(), name="poll", exclusive=True)
        self.set_interval(0.25, self._update_status)
        self.set_interval(0.25, self.refresh_actuators)
        self.layout_panes(self.size.width)
        self.call_after_refresh(self._refresh_body)
        self._update_header()

    def on_resize(self, event: events.Resize) -> None:
        self.layout_panes(event.size.width)
        self.call_after_refresh(self._refresh_body)

    # -- the shell -----------------------------------------------------------
    def shell_catalogue(self) -> Catalogue:
        """The menus for now: starting a recording (``n``) changes the Record menu's items."""
        recording = self.controller.recorder.journal is not None
        if recording not in self._catalogues:
            self._catalogues[recording] = monitor_catalogue(
                recording=recording, actuate=self.controller.actuation is not None
            )
        return self._catalogues[recording]

    def shell_changed(self) -> None:
        self._update_status()

    def command_unavailable(self, cmd: Command) -> str | None:
        c = self.controller
        if cmd.id in SIGNAL_COMMANDS and getattr(self._editor(), "selected", None) is None:
            return SELECT_FIRST
        if cmd.id in ACTUATOR_COMMANDS and not self.actuators_focused():
            if c.actuation is None:
                return NO_ACTUATE
            return f"Show the actuator pane ({key_hint('actuators')}) and pick a row first"
        if cmd.id == "reconnect" and getattr(c, "reconnect", None) is None:
            return "Reconnect unavailable (no reconnector for this session)"
        return None

    def command_state(self, cmd: Command) -> bool | None:
        c = self.controller
        if cmd.id.startswith("view:"):
            return c.view_mode == cmd.id.split(":", 1)[1]
        if cmd.id.startswith("filter:"):
            return getattr(self._editor(), "filter_mode", "all") == cmd.id.split(":", 1)[1]
        flags = None
        if cmd.id in ("verify", "ship"):
            ed = self._editor()
            flags = ed.selected_flags() if hasattr(ed, "selected_flags") else None
        return {
            "pause": self.paused,
            "schedule": getattr(c.schedule, "schedule", "fixed") != "fixed",
            "rulers": bool(c.show_rulers),
            "actuators": self.actuators_shown(),
            "verify": bool(flags and flags[0]),
            "ship": bool(flags and flags[1]),
        }.get(cmd.id)

    def on_status_line_zone_clicked(self, event: StatusLine.ZoneClicked) -> None:
        if event.zone == "poll":
            self.open_menu_above("Poll", x=event.x, bottom=event.y)
        elif event.zone in ZONE_ACTIONS:
            self.call_next(self.run_action, ZONE_ACTIONS[event.zone])

    # -- polling -----------------------------------------------------------
    async def action_quit(self) -> None:
        """Quit, signalling any in-flight reconnect to abort (clean stop)."""
        self._stopping = True
        self.exit()

    def _refresh_body(self) -> None:
        """Repaint the body in place, fitted to the pane, never moving the scroll position."""
        # The poll worker can fire mid-teardown (after quit); ignore if the DOM is gone.
        try:
            scroll = self.screen_stack[0].query_one("#scroll", VerticalScroll)
            body = self.screen_stack[0].query_one("#body", Static)
        except (NoMatches, IndexError):
            return
        # The body's own content width (inside the scrollbar gutter and its padding); before the
        # first layout, the pane's width less those three columns.
        width = body.content_size.width or (
            scroll.content_size.width - 3 if scroll.content_size.width else None
        )
        body.update(self.controller.renderer.render(width, title=False))
        self._update_status()

    def _update_status(self) -> None:
        try:
            base = self.screen_stack[0]
            status = base.query_one("#status", StatusLine)
            context = base.query_one("#context", ContextLine)
        except (NoMatches, IndexError):
            return
        left, right = status_zones(self.controller, paused=self.paused)
        status.set_zones(left, right)
        context.set_line(
            context_items(self.controller, actuators=self.actuators_focused()),
            self.shell_messages.item(),
        )

    def _update_header(self) -> None:
        """The recording's label and states on the menu bar's right."""
        try:
            bar = self.shell_menu_bar()
        except (NoMatches, IndexError):
            return
        bar.set_document(document_items(self.controller))

    # -- actions -----------------------------------------------------------
    def action_scroll(self, delta: int) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_relative(y=delta, animate=False)

    def action_to_top(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_end(animate=False)

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self._update_status()

    # Live poll-rate control, delegated to the controller's PollScheduler: the interval on the
    # fixed schedule, a multiplier on every PID's period on the per-PID one. The poll loop
    # re-asks the schedule while it waits, so a change takes effect at once.
    def action_faster(self) -> None:
        self._flash(f"Poll rate: {self.controller.schedule.faster()}", secs=2.0)

    def action_slower(self) -> None:
        self._flash(f"Poll rate: {self.controller.schedule.slower()}", secs=2.0)

    def action_toggle_schedule(self) -> None:
        self._flash(f"Poll schedule: {self.controller.schedule.toggle()}", secs=2.0)

    def action_toggle_rulers(self) -> None:
        """Toggle the byte-index ruler + the per-signal byte-reference column."""
        self.controller.show_rulers = not self.controller.show_rulers
        self._refresh_body()

    def action_event_log(self) -> None:
        """Open the event log: this run's messages, then its diagnostics events."""
        if self._modal_active():
            return
        self.push_screen(EventLogModal(list(self.shell_messages.history)))

    def action_session_info(self) -> None:
        """Open the recording summary + recording-history overlay."""
        if self._modal_active():
            return
        c = self.controller
        self.push_screen(SessionInfoModal(c.view.session_summary(), list(c.recorder.segments)))

    def action_cycle_view(self) -> None:
        if self._modal_active():
            return
        self.action_set_view(self.controller.view.cycle())

    def action_set_view(self, mode: str) -> None:
        """Show the values in one view mode (``ecus``, ``ranges``, ``signals``, ``full``)."""
        self.controller.view.set(mode)
        self._refresh_body()
        self._flash(f"View: {mode}")

    def action_pid_picker(self) -> None:
        """Open the ECU → PID picker (the bare ``canair monitor`` one) on the live poll."""
        if self._modal_active():
            return
        seed = self.controller.selectors.picker_seed()
        if not seed.choices:
            self._flash("The profile defines no pollable PIDs", "warning")
            return

        def _done(picked: dict[str, list[str]] | None) -> None:
            if picked:
                selectors = self.controller.selectors.apply(picked)
                self._flash(f"Polling: {' '.join(selectors)}", secs=3.0)
            self._refresh_body()
            self._update_header()

        screen = LiveMonitorSelectScreen(
            seed.choices, seed.groups, include_static=seed.include_static, initial=seed.current
        )
        self.push_screen(screen, _done)

    def action_session_type(self) -> None:
        """Open the diagnostic session-type picker (view/switch per ECU).

        The picker only *chooses* an (ecu, mode) pair: the ``10 xx`` send is async, so it
        runs here as a worker after the modal closes.
        """
        if self._modal_active():
            return

        def _done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            ecu, mode = result
            switch = self.controller.sessions.switch
            done = self.controller.foreground.submit(
                f"session {ecu} {mode}", lambda: switch(ecu, mode)
            )
            self.run_worker(self._report_session_switch(done), name="session-switch")

        self.push_screen(SessionPickerScreen(self.controller), _done)

    async def _report_session_switch(self, done) -> None:
        """Say what the switch, run on the foreground lane, came to."""
        try:
            msg = await done
        except Exception as exc:  # a transport failure the switch did not turn into text
            msg = f"Session switch failed: {exc}"
        self._flash(msg, severity_of(msg))

    # -- the view's state -----------------------------------------------------
    def _modal_active(self) -> bool:
        """True when a dialog owns the screen (don't act on the view beneath it)."""
        return len(self.screen_stack) > 1

    def action_stay(self) -> None:
        """Escape outside a pane: nothing to back out of, and never a quit."""

    def action_force_reconnect(self) -> None:
        """Ask the poll loop to rebuild the connection after the current cycle.

        Only sets the flag the loop already watches instead of reconnecting here: an attempt
        started from a key handler would race the in-flight poll for the same terminal.
        """
        c = self.controller
        if self._modal_active() or c.reconnecting:
            return
        if getattr(c, "reconnect", None) is None:
            self._flash("Reconnect unavailable (no reconnector for this session)", "warning")
            return
        c.mark_disconnected("manual")
        self._flash(f"Reconnecting after this poll cycle{ELLIPSIS}")

    def _flash(self, msg: str, severity: Severity = "info", secs: float | None = None) -> None:
        """Say ``msg`` on the bottom line (see :mod:`canair.tui.shell.messages`)."""
        self.message(msg, severity, secs=secs)

    def _flash_result(self, msg: str) -> None:
        """Say the one-line result a controller returned, in the severity its words carry."""
        self._flash(msg, severity_of(msg))


async def run_monitor_app(controller: MonitorController) -> None:
    """Run the monitor TUI to completion (returns when the user quits).

    Stop signals (``SIGTERM``/``SIGHUP`` — a `kill`, a vanished terminal, or the lock watchdog
    standing down) are routed through the app's own quit action, so the TUI tears itself down
    cleanly (terminal restored, ``--save`` journal reconciled) instead of a
    ``KeyboardInterrupt`` unwinding through Textual's internals.
    """

    app = MonitorApp(controller)

    def _request_quit() -> None:
        # Runs on the event loop (asyncio signal handler), so the app can be asked to exit
        # directly; _stopping aborts any in-flight reconnect.
        app._stopping = True
        app.exit()

    with graceful_stop_async(_request_quit):
        await app.run_async()
