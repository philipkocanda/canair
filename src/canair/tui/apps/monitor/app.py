"""Textual TUI for the live monitor (``canair monitor``).

The latest values render into a single widget that updates *in place* inside a
scrollable container. The scroll position is **never** moved by a data refresh:
values keep updating wherever you are, so a byte you are reading can't be yanked
out from under you. ``space`` pauses polling; ``G``/``End`` jumps to the newest
output when you do want the tail.

The monitor doubles as a lightweight PID editor: the arrow keys move a selection
cursor over the decoded signal rows, ``e`` opens an in-place edit dialog
(expression / unit / min / max / notes / verified / ship), ``v`` and ``x``
quick-toggle the selected signal's verified/ship flags, and ``F`` cycles
a display filter (all / verified / unverified / ship / no-ship). Edits are
written through :mod:`canair.yaml_edit.pids` and picked up on the next poll.

With ``--save`` a blinking ``● REC`` (bold, never red: red means an error) marks the active
recording. Because every
payload is journaled and written to a capture file automatically on exit, ``s``
only *labels* the current recording (label / state / notes) — it doesn't write a
separate file. ``n`` **finishes** the current session (writing it to a capture
file now) and starts a fresh, separately-labelled one, so a single run can yield
several sessions. Without ``--save``, ``s`` performs a one-off write of the
payloads captured so far and ``n`` is unavailable.

``V`` cycles the display view mode (``ecus`` → ``ranges`` → ``signals`` →
``full``): a bare ECU list, each signal's captured value span, the decoded
signals only, or the signals plus raw byte payloads (the default). ``i`` opens a
read-only session-info overlay showing the current segment's label/state/notes,
the run's frame/cycle counters and retain mode, where the ``--save`` journal is
being written, and the history of finished ``--save`` segments this run.

``p`` opens the ECU → PID picker — the same screen a bare ``canair monitor``
shows — on what is polled now; applying it swaps the live poll's selectors
without restarting the session. ``t``
opens the diagnostic session-type switcher: pick an ECU, then a session mode —
preferring the ECU's own known-good modes from ``canair scan sessions`` history,
falling back to the safe fixed list (01/03/81/82/83; 02 programmingSession and
85 are never offered). The status bar's ``sess`` item shows each ECU's currently
open session type, since a monitor session is **not** always a plain ``10 03``
(extendedDiagnosticSession) — KWP2000 ECUs need ``10 81`` instead, and the
session-info overlay/status item make the active mode explicit rather than
assumed. Each signal row also shows a small circle before its code: green when
the value was refreshed this cycle, dim when it's stale (cached from an earlier
successful read).

The dialogs (label a recording, edit a signal) are :mod:`canair.tui.apps.monitor.dialogs`;
the overlays and the session-type picker are :mod:`canair.tui.apps.monitor.overlays`; what the
status bar and the header say is :mod:`canair.tui.apps.monitor.status`; the poll loop and the
save keys are the mixins of :mod:`canair.tui.apps.monitor.polling` and
:mod:`canair.tui.apps.monitor.recording`.

The bottom bar is built from :mod:`canair.tui.status`, so it fits itself to the
terminal width instead of clipping its tail on a narrow screen; ``?`` remains
the authoritative key list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from canair.tui.apps.monitor.dialogs import EditParamDialog, SaveDialog
from canair.tui.apps.monitor.overlays import EventLogModal, SessionInfoModal, SessionPickerScreen
from canair.tui.apps.monitor.polling import PollingMixin
from canair.tui.apps.monitor.recording import RecordingMixin
from canair.tui.apps.monitor.status import (
    SELECT_FIRST,
    header_text,
    hint_items,
    metric_items,
)
from canair.tui.apps.monitor_select.app import LiveMonitorSelectScreen
from canair.tui.flash import Flash, Severity, severity_of
from canair.tui.help import HelpMixin
from canair.tui.keys import bind
from canair.tui.scroll import APP_BASE_CSS, reveal_marker
from canair.tui.status import (
    StatusBar,
)

if TYPE_CHECKING:
    from canair.live.monitor.controller import MonitorController


from canair.live.monitor.render import MONITOR_LEGEND
from canair.render.glyphs import ELLIPSIS

__all__ = [
    "EditParamDialog",
    "EventLogModal",
    "MonitorApp",
    "SaveDialog",
    "SessionInfoModal",
    "SessionPickerScreen",
]


class MonitorApp(PollingMixin, RecordingMixin, HelpMixin, App):
    """Scrollable, in-place live-value monitor."""

    HELP_TITLE = "canair monitor — keyboard shortcuts"
    HELP_LEGEND = MONITOR_LEGEND

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = (
        APP_BASE_CSS
        + """
    #header { dock: top; height: auto; max-height: 3; padding: 0 1; background: transparent; }
    """
    )

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("help", "help"),
        *bind("session", "save", desc="save / label"),
        *bind("new", "new_segment", desc="new session"),
        *bind("rulers", "toggle_rulers", desc="byte ruler"),
        *bind("event_log", "event_log"),
        *bind("info", "session_info", desc="session info"),
        *bind("view", "cycle_view"),
        *bind("pause", "toggle_pause"),
        *bind("increase", "faster", desc="poll faster"),
        *bind("decrease", "slower", desc="poll slower"),
        *bind("mode", "toggle_schedule", desc="poll schedule"),
        # Down/up and j/k are the same movement: the signal cursor, which falls
        # back to plain scrolling when nothing is selectable. priority beats the
        # focused scroll container's own arrow handling.
        *bind("move_down", "select(1)", desc="select down", show=False, priority=True),
        *bind("move_up", "select(-1)", desc="select up", show=False, priority=True),
        *bind("axis_start", "to_top", desc="top", show=False),
        *bind("axis_end", "to_bottom", desc="bottom", show=False),
        *bind("back", "clear_selection", desc="back", show=False),
        *bind("edit", "edit"),
        *bind("verify", "verify"),
        *bind("exclude", "toggle_ship", desc="toggle ship"),
        *bind("filter_cycle", "cycle_filter"),
        *bind("reconnect", "force_reconnect"),
        *bind("pick", "pid_picker", desc="select ECUs/PIDs"),
        *bind("value_type", "session_type", desc="session type"),
    ]

    def __init__(self, controller: MonitorController):
        super().__init__()
        self.controller = controller
        self.paused = False
        # Set when the user asks to quit, so an in-flight reconnect aborts cleanly.
        self._stopping = False
        self.flash = Flash()

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="header", markup=True)
        with VerticalScroll(id="scroll"):
            yield Static(self.controller.renderer.render(), id="body", markup=False)
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        # Use the terminal's own palette + default background (ansi_default)
        # rather than Textual's remapped truecolor theme + grey surface. Keeps
        # byte colours matching the plain output and readable on any themed
        # terminal (iTerm2, Termius, …). CSS `background: transparent` covers the
        # background regardless; the theme fixes the colour mapping.
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        # Let the controller repaint mid-cycle as each PID resolves, so a slow /
        # timing-out PID never freezes the whole view (only its own row lags).
        self.controller.on_partial = self._refresh_body
        self.run_worker(self._poll_loop(), name="poll", exclusive=True)
        self.set_interval(0.25, self._update_status)
        self._update_status()
        self._update_header()

    # -- polling -----------------------------------------------------------
    async def action_quit(self) -> None:
        """Quit, signalling any in-flight reconnect to abort (clean stop)."""
        self._stopping = True
        self.exit()

    def _refresh_body(self) -> None:
        """Repaint the body in place, never moving the user's scroll position."""
        # The poll worker can fire mid-teardown (after quit); ignore if the DOM
        # is already gone.
        try:
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        body.update(self.controller.renderer.render())
        self._update_status()

    def _update_status(self) -> None:
        try:
            status = self.query_one("#status", StatusBar)
        except NoMatches:
            return
        status.set_lines(
            metric_items(self.controller, paused=self.paused, flash=self.flash.item()),
            hint_items(self.controller),
        )

    def _editor(self):
        """The controller's edit collaborator, or None (older/fake controllers)."""
        return getattr(self.controller, "editor", None)

    def _update_header(self) -> None:
        """Render the top bar (see :func:`~canair.tui.apps.monitor.status.header_text`)."""
        try:
            header = self.query_one("#header", Static)
        except NoMatches:
            return
        header.update(header_text(self.controller, self.size.width))

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

    # Live poll-rate control, delegated to the controller's PollScheduler: the
    # interval on the fixed schedule, a multiplier on every PID's period on the
    # per-PID one. The poll loop re-asks the schedule while it waits, so a change
    # takes effect at once. Clamped by the scheduler.
    def action_faster(self) -> None:
        self._flash(f"Poll rate: {self.controller.schedule.faster()}", secs=2.0)

    def action_slower(self) -> None:
        self._flash(f"Poll rate: {self.controller.schedule.slower()}", secs=2.0)

    def action_toggle_schedule(self) -> None:
        self._flash(f"Poll schedule: {self.controller.schedule.toggle()}", secs=2.0)
        self._update_status()

    def action_toggle_rulers(self) -> None:
        """Toggle the byte-index ruler + the per-signal byte-reference column."""
        self.controller.show_rulers = not self.controller.show_rulers
        self._refresh_body()

    def action_event_log(self) -> None:
        """Open the scrollable diagnostics event-log overlay."""
        if self._modal_active():
            return
        self.push_screen(EventLogModal())

    def action_session_info(self) -> None:
        """Open the session summary + segment-history overlay."""
        if self._modal_active():
            return
        c = self.controller
        summary = c.view.session_summary()
        segments = list(c.recorder.segments)
        self.push_screen(SessionInfoModal(summary, segments))

    def action_cycle_view(self) -> None:
        """Cycle the display view mode (ecus → ranges → signals → full)."""
        if self._modal_active():
            return
        mode = self.controller.view.cycle()
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

        The picker itself only *chooses* an (ecu, mode) pair — the ``10 xx``
        send is async, so it runs here as a worker after the modal closes.
        """
        if self._modal_active():
            return

        def _done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            ecu, mode = result
            self.run_worker(self._apply_session_switch(ecu, mode), name="session-switch")

        self.push_screen(SessionPickerScreen(self.controller), _done)

    async def _apply_session_switch(self, ecu: str, mode: str) -> None:
        msg = await self.controller.sessions.switch(ecu, mode)
        self._flash(msg, severity_of(msg))
        self._update_status()

    # -- selection / in-place editing --------------------------------------
    def _last_queries(self):
        return getattr(self.controller, "last_queries", [])

    def _modal_active(self) -> bool:
        """True when a dialog owns the screen (don't act on the view beneath it)."""
        return len(self.screen_stack) > 1

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # The signal cursor's arrow bindings are priority bindings, which run
        # before the focused widget — so while a dialog is up they must stand
        # down, or the dialog's own list could never be moved.
        if action == "select" and self._modal_active():
            return False
        return True

    def action_select(self, delta: int) -> None:
        """Move the signal-selection cursor and scroll it into view.

        Falls back to plain scrolling when there is no editor or nothing is
        selectable, so the arrow keys never feel dead.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or ed.move(self._last_queries(), delta) is None:
            self.action_scroll(delta)
            return
        self._refresh_body()
        self._scroll_to_selection()
        self._update_status()

    def action_clear_selection(self) -> None:
        """Drop the ▶ signal cursor so ↑/↓ resume plain scrolling.

        No-ops (letting escape fall through) when a modal owns the screen or
        nothing is selected, so it never steals escape from a dialog.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or not getattr(ed, "clear_selection", None):
            return
        if not ed.clear_selection():
            return
        self._refresh_body()
        self._flash("Selection cleared")

    def action_cycle_filter(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        mode = ed.cycle_filter(self._last_queries())
        self._refresh_body()
        self._flash(f"Filter: {mode}")

    def action_force_reconnect(self) -> None:
        """Ask the poll loop to rebuild the connection after the current cycle.

        Deliberately only sets the flag the loop already watches instead of
        reconnecting here: an attempt started from a key handler would race the
        in-flight poll for the same terminal.
        """
        c = self.controller
        if self._modal_active() or c.reconnecting:
            return
        if getattr(c, "reconnect", None) is None:
            self._flash("Reconnect unavailable (no reconnector for this session)", "warning")
            return
        c.mark_disconnected("manual")
        self._flash(f"Reconnecting after this poll cycle{ELLIPSIS}")

    def action_verify(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash(SELECT_FIRST, "warning")
            return
        self._flash_result(ed.toggle_verified())
        self._refresh_body()

    def action_toggle_ship(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash(SELECT_FIRST, "warning")
            return
        self._flash_result(ed.toggle_ship())
        self._refresh_body()

    def action_edit(self) -> None:
        """Open the edit dialog for the selected signal (polling pauses)."""
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        target = ed.edit_target() if hasattr(ed, "edit_target") else None
        if not target:
            self._flash(SELECT_FIRST, "warning")
            return
        # Auto-pause polling while the modal is open; restore prior state after.
        was_paused = self.paused
        self.paused = True

        def _done(result: dict | None) -> None:
            self.paused = was_paused
            if result is None:
                self._flash("Edit cancelled")
                return
            try:
                msg = ed.apply_edit(result)
            except Exception as exc:  # keep the TUI alive on any edit error
                msg = f"Edit failed: {exc}"
            self._flash_result(msg)
            self._refresh_body()

        self.push_screen(EditParamDialog(target), _done)

    def _scroll_to_selection(self) -> None:
        """Scroll so the ``▶`` selection cursor is within the viewport."""
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        reveal_marker(scroll, body)

    def _flash(self, msg: str, severity: Severity = "info", secs: float = 5.0) -> None:
        """Show a transient message in the status line for ``secs`` seconds."""
        self.flash.show(msg, severity, secs=secs)
        self._update_status()

    def _flash_result(self, msg: str) -> None:
        """Flash the one-line result a controller returned, in the severity its words carry."""
        self._flash(msg, severity_of(msg))


async def run_monitor_app(controller: MonitorController) -> None:
    """Run the monitor TUI to completion (returns when the user quits).

    Stop signals (``SIGTERM``/``SIGHUP`` — a `kill`, a vanished terminal, or the
    lock watchdog standing down) are routed through the app's own quit action, so
    the TUI tears itself down cleanly (terminal restored, ``--save`` journal
    reconciled) instead of a ``KeyboardInterrupt`` unwinding through Textual's
    internals.
    """
    from canair.core.stop_signals import graceful_stop_async

    app = MonitorApp(controller)

    def _request_quit() -> None:
        # Runs on the event loop (asyncio signal handler), so the app can be
        # asked to exit directly; _stopping aborts any in-flight reconnect.
        app._stopping = True
        app.exit()

    with graceful_stop_async(_request_quit):
        await app.run_async()
