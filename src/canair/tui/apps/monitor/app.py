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
the overlays and the session-type picker are :mod:`canair.tui.apps.monitor.overlays`.

The bottom bar is built from :mod:`canair.tui.status`, so it fits itself to the
terminal width instead of clipping its tail on a narrow screen; ``?`` remains
the authoritative key list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from canair.tui.apps.monitor.dialogs import EditParamDialog, SaveDialog
from canair.tui.apps.monitor.overlays import EventLogModal, SessionInfoModal, SessionPickerScreen
from canair.tui.apps.monitor_select.app import LiveMonitorSelectScreen
from canair.tui.flash import Flash, Severity, severity_of
from canair.tui.help import HelpMixin
from canair.tui.keys import ARROWS, PAGE_HINT, arrows_hint, bind, key_hint
from canair.tui.scroll import APP_BASE_CSS, reveal_marker
from canair.tui.status import (
    P_ESSENTIAL,
    P_HIGH,
    P_LOW,
    P_NORMAL,
    StatusBar,
    StatusItem,
    hint_item,
)

if TYPE_CHECKING:
    from canair.live.monitor.controller import MonitorController

import asyncio
import functools
import time
from datetime import datetime

from canair.core.eventlog import log_event, log_exception
from canair.live.monitor.backoff import summarize_slowed
from canair.live.monitor.render import MONITOR_LEGEND
from canair.render.glyphs import CURSOR, ELLIPSIS
from canair.states import join_states
from canair.transport.errors import transport_error_types
from canair.tui import styles

__all__ = [
    "EditParamDialog",
    "EventLogModal",
    "MonitorApp",
    "SaveDialog",
    "SessionInfoModal",
    "SessionPickerScreen",
]

#: The hint for the ECU/PID picker key, shown in the header and the status bar.
PICK_HINT = key_hint("pick", "select ECUs/PIDs")
SELECT_FIRST = f"Select a signal first with {ARROWS}"


# Round trip above which the health line reports it. Below this the link is not
# what is limiting a poll cycle and the number is only noise; above it, latency is
# the explanation for a slow or drop-prone session and the driver should see it.
_RTT_NOTABLE_S = 0.05


def _link_items(controller) -> list[StatusItem]:
    """Measured link round trip, once there is enough evidence to show one.

    Only shown when the link is slow enough to matter: on a LAN the number is
    noise, and every always-on segment costs a column a real signal could use.
    Tolerates a controller with no estimator (the raw client has none yet).
    """
    link_fn = getattr(controller, "link", None)
    link = link_fn() if callable(link_fn) else None
    rtt = getattr(link, "rtt", None) if link is not None else None
    if rtt is None or rtt < _RTT_NOTABLE_S:
        return []
    return [StatusItem(f"[dim]rtt[/] [{styles.WARNING}]{rtt * 1000:.0f}ms[/]", P_NORMAL)]


class MonitorApp(HelpMixin, App):
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

    def _note_pause(self, on: bool) -> None:
        """Record that polling actually stopped (``on``) or resumed.

        Called by the poll loop rather than wherever ``paused`` is set (the space key,
        a modal dialog), because a cycle already in flight finishes first: the gap in
        the recording starts when polling stops, not when it was asked to.
        """
        tracker = getattr(self.controller, "link_events", None)
        if tracker is not None:
            tracker.paused(on)

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="header", markup=True)
        with VerticalScroll(id="scroll"):
            yield Static(self.controller.render(), id="body", markup=False)
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
        self.controller._on_partial = self._refresh_body
        self.run_worker(self._poll_loop(), name="poll", exclusive=True)
        self.set_interval(0.25, self._update_status)
        self._update_status()
        self._update_header()

    # -- polling -----------------------------------------------------------
    async def action_quit(self) -> None:
        """Quit, signalling any in-flight reconnect to abort (clean stop)."""
        self._stopping = True
        self.exit()

    async def _poll_loop(self) -> None:
        stopped = False
        while True:
            if self.paused != stopped:
                stopped = self.paused
                self._note_pause(stopped)
            # Chunked wait, re-asking the schedule each time, so pause/quit stay
            # responsive and a live rate change or a just-added PID takes effect at
            # once rather than after a sleep chosen before it.
            if self.paused or self.controller.seconds_until_due() > 0:
                await asyncio.sleep(0.05)
                continue
            try:
                await self.controller.poll_once()
            except transport_error_types() as e:
                # The link died mid-cycle. Same handling as a poll that set
                # `disconnected` itself: re-home rather than abandon the run.
                log_event(
                    "bus",
                    f"poll cycle failed: {type(e).__name__}: {e}",
                    transport=self.controller.transport_type,
                )
                self.controller.mark_disconnected("error")
            except Exception as e:
                # Not the transport — a genuine internal fault. Reconnecting
                # would not fix it and would hide it, so stop cleanly rather
                # than leaving a silently-frozen UI.
                log_exception("unexpected monitor poll failure", e)
                self.controller.disconnected = True
                self.exit()
                return
            if self.controller.disconnected:
                # Try to re-home the dropped session (auto-failover / --wait)
                # in place, keeping the --save journal recording across the gap.
                if not await self._attempt_reconnect():
                    self.exit()
                    return
                self._refresh_body()
                continue
            self._refresh_body()
            # Yield even when the next tick is already due (a cycle that overran
            # its period), so back-to-back polls never starve the UI.
            await asyncio.sleep(0.05)

    async def _attempt_reconnect(self) -> bool:
        """Re-home a dropped session; True to resume, False to exit the app.

        On a give-up the controller stays ``disconnected`` so ``mode_monitor``
        reports the drop; a user quit during the attempt clears it for a clean stop.
        """
        c = self.controller
        reconnect = getattr(c, "reconnect", None)
        if reconnect is None:
            return False

        def _notice(msg: str) -> None:  # thread-safe: plain attribute write
            c.reconnect_note = msg

        c.reconnecting = True
        c.reconnect_note = "connection dropped — reconnecting…"
        self._update_status()
        try:
            ok = await reconnect(
                c, getattr(c, "session_steps", None), stop=lambda: self._stopping, notice=_notice
            )
        finally:
            c.reconnecting = False
        if not ok and self._stopping:
            c.disconnected = False  # deliberate stop, not a failure
        return ok

    def _refresh_body(self) -> None:
        """Repaint the body in place, never moving the user's scroll position."""
        # The poll worker can fire mid-teardown (after quit); ignore if the DOM
        # is already gone.
        try:
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        body.update(self.controller.render())
        self._update_status()

    def _update_status(self) -> None:
        try:
            status = self.query_one("#status", StatusBar)
        except NoMatches:
            return
        status.set_lines(self._metric_items(), self._hint_items())

    def _metric_items(self) -> list[StatusItem]:
        """First status line: what the run is doing right now, most vital first."""
        c = self.controller
        items: list[StatusItem] = []

        # Reconnecting banner (auto-failover / --wait): while a reconnect attempt
        # is in flight, lead the status line with its live note.
        if getattr(c, "reconnecting", False):
            note = getattr(c, "reconnect_note", "") or f"reconnecting{ELLIPSIS}"
            items.append(StatusItem(f"[b {styles.WARNING}]⟳ {note}[/]", P_ESSENTIAL))
        # Blinking ● REC while a --save journal is active. The dot pulses ~1s
        # on / 1s off, derived from wall-clock time so the rate is independent of
        # the status tick (0.25s); the label stays put so it doesn't jump layout.
        # Bold, not red: red means an error.
        if getattr(c, "journal", None) is not None:
            dot_style = styles.RECORDING if int(time.monotonic()) % 2 == 0 else styles.MUTED
            items.append(StatusItem(f"[{dot_style}]●[/] [{styles.RECORDING}]REC[/]", P_ESSENTIAL))
        if self.paused:
            items.append(StatusItem("[reverse] PAUSED [/]", P_ESSENTIAL))
        items.append(StatusItem(f"[dim]cycle[/] {c.cycle}", P_NORMAL))
        items.append(StatusItem(f"{c.schedule.describe()}", P_NORMAL))
        backoff = getattr(c, "backoff", None)
        slowed = backoff.slowed() if backoff is not None else {}
        if slowed:
            items.append(
                StatusItem(f"[dim]slow[/] [{styles.WARNING}]{summarize_slowed(slowed)}[/]", P_HIGH)
            )
        items.append(StatusItem(f"{c.elapsed:.1f}[dim]s[/]", P_LOW))
        # ELM path reports commands + time spent in the ELM327; the raw path
        # reports UDS requests (no ELM involved).
        if getattr(c, "raw", False):
            items.append(StatusItem(f"{c.last_cmds}[dim] req[/]", P_LOW))
        else:
            items.append(
                StatusItem(
                    f"{c.last_cmds}[dim] cmds ·[/] {c.last_elm_time:.1f}[dim]s ELM[/]", P_LOW
                )
            )
        # Captured (all fresh payloads) vs unique (distinct values kept) — the two
        # differ from the on-screen row count (one row per polled PID).
        captured = getattr(c, "total_frames", 0)
        uniq = getattr(c, "unique_frames", 0)
        items.append(StatusItem(f"[dim]captured[/] {captured}[dim]/uniq[/] {uniq}", P_NORMAL))
        items.extend(self._health_items())
        items.extend(self._session_items())
        # Live auto-suggested vehicle state (from decoded values), if any. One slot,
        # so this is the MOST SPECIFIC matching state (DRIVING, not the READY it
        # implies) — see MonitorController.suggested_state.
        state_fn = getattr(c, "suggested_state", None)
        if callable(state_fn):
            try:
                state = state_fn()
            except Exception:
                state = None
            if state:
                items.append(StatusItem(f"[dim]state[/] [b]{state}[/]", P_HIGH))
        flash = self.flash.item()
        if flash is not None:
            items.append(flash)
        return items

    def _health_items(self) -> list[StatusItem]:
        """Transport-health segments: dropped frames, pipe desyncs, other errors.

        Drops are the data-integrity headline (the reassembly corruption that
        silently poisoned historical captures), so a live burst also shows a
        per-cycle ``+N`` spike. ``stale`` is broken out rather than folded into
        drops because it means something different and actionable — replies
        landing in the wrong request's slot — and ``resync`` next to it shows the
        transport realigning itself, which is how a marginal link looks while it
        is still coping. All are omitted when zero, keeping a clean run
        uncluttered.
        """
        diag_fn = getattr(self.controller, "diag", None)
        diag = diag_fn() if callable(diag_fn) else None
        if diag is None:
            return []
        stale = getattr(diag, "stale", 0)
        drops = diag.drops - stale
        resyncs = getattr(diag, "resyncs", 0)
        # Non-drop errors (timeouts/bus/decode) — disjoint from drops and stale.
        errs = diag.errors - drops - stale
        items: list[StatusItem] = []
        if drops:
            spike = max(getattr(self.controller, "last_drops", 0) - self._last_stale(), 0)
            spike_txt = f" [b {styles.ERROR}]+{spike}[/]" if spike else ""
            items.append(
                StatusItem(f"[dim]drops[/] [b {styles.ERROR}]{drops}[/]{spike_txt}", P_HIGH)
            )
        if stale:
            spike = self._last_stale()
            spike_txt = f" [b {styles.ERROR}]+{spike}[/]" if spike else ""
            items.append(
                StatusItem(f"[dim]stale[/] [b {styles.ERROR}]{stale}[/]{spike_txt}", P_HIGH)
            )
        if resyncs:
            items.append(StatusItem(f"[dim]resync[/] [{styles.WARNING}]{resyncs}[/]", P_HIGH))
        if errs:
            items.append(StatusItem(f"[dim]errs[/] [{styles.WARNING}]{errs}[/]", P_HIGH))
        items.extend(_link_items(self.controller))
        return items

    def _last_stale(self) -> int:
        return getattr(self.controller, "last_stale", 0)

    def _session_items(self) -> list[StatusItem]:
        """Diagnostic session-type segment: which ECUs have a ``10 xx`` session open.

        One combined item (not one per ECU) to stay compact on a multi-ECU
        query. Omitted entirely when nothing is tracked — e.g. before any
        ``session`` step has run at all. Press ``t`` to view/switch.
        """
        modes_fn = getattr(self.controller, "active_session_modes", None)
        modes = modes_fn() if callable(modes_fn) else {}
        if not modes:
            return []
        parts = ", ".join(f"{ecu}:{mode}" for ecu, mode in sorted(modes.items()))
        return [StatusItem(f"[dim]sess[/] [b]{parts}[/]", P_NORMAL)]

    def _editor(self):
        """The controller's edit collaborator, or None (older/fake controllers)."""
        return getattr(self.controller, "editor", None)

    def _update_header(self) -> None:
        """Render the top bar: current segment title + optional note/states.

        Title is the user-set label (falling back to the polled-selectors
        summary), so it's meaningful even without --save. The note line is
        omitted when empty, keeping the header a single line by default.
        """
        try:
            header = self.query_one("#header", Static)
        except NoMatches:
            return
        c = self.controller
        title_fn = getattr(c, "segment_title", None)
        title = title_fn() if callable(title_fn) else getattr(c, "session_label", "")
        states = getattr(c, "session_states", None) or []
        notes = (getattr(c, "session_notes", "") or "").replace("\n", " ").strip()

        line1 = f"[b]{escape(title or 'Monitor')}[/]"
        if states:
            line1 += f"  [dim]· {', '.join(states)}[/]"
        line1 += f"  [dim]·[/] [b]{PICK_HINT}[/]"
        lines = [line1]
        if notes:
            width = max(20, (self.size.width or 80) - 4)
            if len(notes) > width:
                notes = notes[: width - 1] + "…"
            lines.append(f"[dim]{notes}[/]")
        header.update("\n".join(lines))

    def _hint_items(self) -> list[StatusItem]:
        """Second status line: the current selection/filter plus the key hints.

        Ranked so a narrow terminal keeps the escape hatches (``? help``,
        ``q quit``), the recording controls and the current context, and drops
        the browsing hints first — ``?`` is the complete key list either way.
        """
        c = self.controller
        # While recording (--save), `s` labels the session and `n` finishes it and
        # starts a new one; without --save, `s` is a one-off save and `n` is N/A.
        recording = getattr(c, "journal", None) is not None
        items: list[StatusItem] = []

        ed = self._editor()
        label = ed.selection_label() if ed is not None and hasattr(ed, "selection_label") else ""
        if label:
            items.append(StatusItem(f"[b]{CURSOR} {escape(label)}[/]", P_HIGH))
        if ed is not None:
            filt = getattr(ed, "filter_mode", "all")
            if filt != "all":
                items.append(StatusItem(f"[dim]filter[/] [b]{filt}[/]", P_HIGH))
        # Surface the active view mode when it's not the default full view.
        view = getattr(c, "view_mode", "full")
        if view and view != "full":
            items.append(StatusItem(f"[dim]view[/] [b]{view}[/]", P_HIGH))

        # Changing what is polled is the key a newcomer most needs and would not
        # guess, so it leads the hints, undimmed, and outlives the other keys.
        items.append(StatusItem(f"[b]{PICK_HINT}[/]", P_HIGH))
        items.append(hint_item(key_hint("session", "label" if recording else "save"), P_HIGH))
        if recording:
            items.append(hint_item(key_hint("new", "new session"), P_HIGH))
        if ed is not None:
            items.append(hint_item(arrows_hint("select"), P_NORMAL))
            items.append(hint_item(key_hint("edit"), P_NORMAL))
            items.append(hint_item(key_hint("verify"), P_LOW))
            items.append(hint_item(key_hint("exclude", "toggle ship"), P_LOW))
            items.append(hint_item(key_hint("filter_cycle"), P_LOW))
        else:
            items.append(hint_item(arrows_hint("scroll"), P_LOW))
            items.append(hint_item(PAGE_HINT, P_LOW))
        # The keys nothing else hints at (a view/ruler/overlay) rank above the
        # ones a user would try anyway (space to pause, arrows to scroll).
        items.append(hint_item(key_hint("view"), P_NORMAL))
        items.append(hint_item(key_hint("rulers", "byte ruler"), P_NORMAL))
        items.append(hint_item(key_hint("info", "session info"), P_NORMAL))
        items.append(hint_item(key_hint("value_type", "session type"), P_NORMAL))
        items.append(hint_item(key_hint("event_log"), P_LOW))
        items.append(hint_item(key_hint("pause"), P_LOW))
        if label:
            items.append(hint_item(key_hint("back", "deselect"), P_LOW))
        items.append(hint_item(key_hint("help"), P_ESSENTIAL))
        items.append(hint_item(key_hint("quit"), P_ESSENTIAL))
        return items

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
        summary_fn = getattr(c, "session_summary", None)
        summary = summary_fn() if callable(summary_fn) else {}
        history_fn = getattr(c, "segment_history", None)
        segments = history_fn() if callable(history_fn) else []
        self.push_screen(SessionInfoModal(summary, segments))

    def action_cycle_view(self) -> None:
        """Cycle the display view mode (ecus → ranges → signals → full)."""
        if self._modal_active():
            return
        cycle_fn = getattr(self.controller, "cycle_view", None)
        if not callable(cycle_fn):
            return
        mode = cycle_fn()
        self._refresh_body()
        self._flash(f"View: {mode}")

    def action_pid_picker(self) -> None:
        """Open the ECU → PID picker (the bare ``canair monitor`` one) on the live poll."""
        if self._modal_active():
            return
        seed = self.controller.picker_seed()
        if not seed.choices:
            self._flash("The profile defines no pollable PIDs", "warning")
            return

        def _done(picked: dict[str, list[str]] | None) -> None:
            if picked:
                selectors = self.controller.apply_selection(picked)
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
        msg = await self.controller.switch_session(ecu, mode)
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

    def _suggested_metadata(
        self, *, next_segment: bool = False
    ) -> tuple[str, str, list[tuple[str, str]] | None]:
        """Gather (suggested_label, suggested_state, state_options) for a dialog.

        Label pre-fills from the active query (e.g. "BCM VCU:2101"), falling back
        to a timestamp. State comes from ``offer_states``: the union observed over
        the span being saved, or — for ``next_segment`` (the ``n`` dialog, which
        labels the segment about to start) — the state active now.
        """
        suggested = ""
        label_fn = getattr(self.controller, "query_label", None)
        if callable(label_fn):
            suggested = label_fn()
        if not suggested:
            suggested = f"Monitor {datetime.now():%H:%M}"

        suggested_state = ""
        offer_fn = getattr(self.controller, "offer_states", None)
        if callable(offer_fn):
            state_fn = functools.partial(offer_fn, next_segment=next_segment)
        else:
            state_fn = getattr(self.controller, "suggested_states", None)
        if callable(state_fn):
            try:
                suggested_state = join_states(state_fn())
            except Exception:
                suggested_state = ""

        state_options = None
        options_fn = getattr(self.controller, "state_options", None)
        if callable(options_fn):
            try:
                state_options = options_fn()
            except Exception:
                state_options = None
        return suggested, suggested_state, state_options

    def action_save(self) -> None:
        """Label the recording (--save) or write captures now (no --save).

        With --save active every payload is already journaled and written to a
        capture file automatically on exit, so ``s`` only *labels* the current
        recording (label / state / notes). Without --save it performs an on-demand
        write of the payloads captured so far to a new capture file.
        """
        if self._modal_active():
            return
        if not self.controller.has_captures():
            self._flash("No payloads captured yet — nothing to save", "warning")
            return
        suggested, suggested_state, state_options = self._suggested_metadata()
        recording = getattr(self.controller, "journal", None) is not None
        if recording:
            title = "Label recording"
            caption = (
                "This recording is [b]already being saved[/] — every payload is "
                "written automatically when you quit or start a new session.\n"
                "[dim]Here you only set its label / state / notes. "
                "Use [b]n[/b] to finish this session and start a fresh one.[/]"
            )
            save_button = "Set label"
        else:
            title = "Save captures now"
            caption = (
                "Writes the payloads captured so far to a [b]new capture file[/].\n"
                "[dim](Not recording with --save — this is a one-off snapshot save.)[/]"
            )
            save_button = "Save"

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Cancelled — nothing saved")
                return
            label, state, notes = result
            try:
                msg = self.controller.save_now(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any save error
                msg = f"Save failed: {exc}"
            self._flash_result(msg)
            self._update_header()

        self.push_screen(
            SaveDialog(
                suggested,
                suggested_state,
                state_options,
                title=title,
                caption=caption,
                save_button=save_button,
            ),
            _done,
        )

    def action_new_segment(self) -> None:
        """Finish the current --save session (write it now) and start a fresh one."""
        if self._modal_active():
            return
        if getattr(self.controller, "journal", None) is None:
            self._flash("Start a new session needs --save (nothing is being recorded)", "warning")
            return
        suggested, suggested_state, state_options = self._suggested_metadata(next_segment=True)

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Cancelled — current session kept recording")
                return
            label, state, notes = result
            try:
                msg = self.controller.new_segment(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any error
                msg = f"Could not start new session: {exc}"
            self._flash_result(msg)
            self._update_header()

        self.push_screen(
            SaveDialog(
                suggested,
                suggested_state,
                state_options,
                title="Finish session & start a new one",
                caption=(
                    "[b]Writes the current session to a capture file now[/], then "
                    "starts a fresh recording with the label below.\n"
                    "[dim]One monitor run can produce several separately-labelled "
                    "sessions this way.[/]"
                ),
                save_button="Finish & start new",
            ),
            _done,
        )


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
