"""The monitor's commands, listed once, for every mockup.

Each direction reaches the same commands (a menu, a palette, a function-key bar), so the
comparison is about the chrome and not about what each one happens to offer. The keys come from
the real keymap (``canair.tui.keys``); a command with ``key`` set needs a key role that does not
exist yet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fake import FILTERS, VIEW_MODES, FakeMonitor

from canair.tui.keys import ROLES, display_key


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    menu: str
    role: str | None = None
    key: str | None = None
    help: str = ""
    #: ``action``, ``toggle`` (shows ``[x]``) or ``choice`` (one of a set, shows ``(•)``).
    kind: str = "action"
    #: ``selection`` or ``actuate``: what the command needs before it is enabled.
    needs: str = ""

    @property
    def key_label(self) -> str:
        if self.key:
            return self.key
        if self.role:
            return display_key(ROLES[self.role].keys[0])
        return ""


SEP = None

MENUS: dict[str, list[Command | None]] = {
    "Record": [
        Command(
            "label",
            "Label recording…",
            "Record",
            "session",
            help="Name the recording, pick its vehicle states, add notes",
        ),
        Command(
            "new",
            "Finish and start new",
            "Record",
            "new",
            help="Write this recording to a capture file and start the next one",
        ),
        Command(
            "info",
            "Recording info…",
            "Record",
            "info",
            help="Label, states, journal path and the recordings of this run",
        ),
        SEP,
        Command(
            "event_log",
            "Event log…",
            "Record",
            "event_log",
            help="Link events, desyncs, reconnects and errors",
        ),
        SEP,
        Command("quit", "Quit", "Record", "quit", help="Write the recording and exit"),
    ],
    "Poll": [
        Command(
            "pause",
            "Pause polling",
            "Poll",
            "pause",
            kind="toggle",
            help="Stop sending requests; the values hold",
        ),
        Command(
            "pick",
            "Select ECUs and PIDs…",
            "Poll",
            "pick",
            help="Change what is polled without restarting",
        ),
        SEP,
        Command("faster", "Poll faster", "Poll", "increase", help="Halve the poll interval"),
        Command("slower", "Poll slower", "Poll", "decrease", help="Double the poll interval"),
        Command(
            "schedule",
            "Per-PID schedule",
            "Poll",
            "mode",
            kind="toggle",
            help="Poll each PID at its own period instead of one fixed interval",
        ),
        SEP,
        Command(
            "diag_session",
            "Diagnostic session…",
            "Poll",
            "value_type",
            help="Open a UDS/KWP diagnostic session type on one ECU",
        ),
        Command(
            "reconnect",
            "Reconnect",
            "Poll",
            "reconnect",
            help="Rebuild the link after this poll cycle",
        ),
    ],
    "View": [
        *(
            Command(
                f"view:{m}",
                {
                    "ecus": "ECUs only",
                    "ranges": "Value ranges",
                    "signals": "Signals",
                    "full": "Signals and bytes",
                }[m],
                "View",
                kind="choice",
                help=f"View mode: {m}",
            )
            for m in VIEW_MODES
        ),
        Command("view_next", "Next view mode", "View", "view", help="Cycle the view mode"),
        SEP,
        *(
            Command(
                f"filter:{f}",
                f"Show {f} signals" if f != "all" else "Show all signals",
                "View",
                kind="choice",
                help=f"Filter: {f}",
            )
            for f in FILTERS
        ),
        Command("filter_next", "Next filter", "View", "filter_cycle", help="Cycle the filter"),
        SEP,
        Command(
            "rulers",
            "Byte ruler",
            "View",
            "rulers",
            kind="toggle",
            help="Byte-index ruler and each signal's byte reference",
        ),
        Command(
            "actuators",
            "Actuator pane",
            "View",
            "actuators",
            kind="toggle",
            needs="actuate",
            help="The outputs and routines of the --actuate ECUs",
        ),
    ],
    "Signal": [
        Command(
            "edit",
            "Edit signal…",
            "Signal",
            "edit",
            needs="selection",
            help="Expression, unit, range, notes, verified, ship",
        ),
        Command(
            "verify",
            "Verified",
            "Signal",
            "verify",
            kind="toggle",
            needs="selection",
            help="Mark the selected signal verified",
        ),
        Command(
            "ship",
            "Ship to AutoPID",
            "Signal",
            "exclude",
            kind="toggle",
            needs="selection",
            help="Include the signal in the device's AutoPID profile",
        ),
        SEP,
        Command(
            "deselect",
            "Deselect",
            "Signal",
            "back",
            needs="selection",
            help="Drop the signal cursor",
        ),
    ],
    "Actuators": [
        Command(
            "act_toggle",
            "Toggle output",
            "Actuators",
            "accept",
            needs="actuate",
            help="Switch the focused output on or off",
        ),
        Command(
            "act_off",
            "Send OFF",
            "Actuators",
            "actuate_off",
            needs="actuate",
            help="Hand the output back to the ECU",
        ),
        Command("act_set", "Set value…", "Actuators", "set_value", needs="actuate"),
        SEP,
        Command("routine_start", "Start routine", "Actuators", "start_routine", needs="actuate"),
        Command("routine_stop", "Stop routine", "Actuators", "stop_routine", needs="actuate"),
    ],
    "Help": [
        Command("help", "Keyboard shortcuts", "Help", "help", help="Every key, by pane"),
        Command("legend", "Colour legend", "Help", help="What each colour and glyph means"),
        Command(
            "palette", "Command palette", "Help", key="ctrl+p", help="Search every command by name"
        ),
    ],
}

COMMANDS = {c.id: c for items in MENUS.values() for c in items if c is not None}


def enabled(m: FakeMonitor, cmd: Command) -> bool:
    if cmd.needs == "selection":
        return m.selected is not None
    if cmd.needs == "actuate":
        return m.actuate
    return True


def state(m: FakeMonitor, cmd: Command) -> bool | None:
    """Whether a toggle or a choice is on; ``None`` for a plain action."""
    if cmd.kind == "choice":
        group, value = cmd.id.split(":")
        return (m.view_mode if group == "view" else m.filter_mode) == value
    detail = m.selected_detail()
    return {
        "pause": m.paused,
        "schedule": m.schedule == "per-PID",
        "rulers": m.show_rulers,
        "actuators": getattr(m, "show_actuators", False),
        "verify": bool(detail and detail["verified"]),
        "ship": bool(detail and detail["ship"]),
    }.get(cmd.id)


def run(m: FakeMonitor, cmd_id: str) -> tuple[str, str]:
    """Do what the mockup can do for ``cmd_id``; return a flash message and its severity."""
    cmd = COMMANDS[cmd_id]
    if not enabled(m, cmd):
        need = "Select a signal first" if cmd.needs == "selection" else "Needs --actuate"
        return need, "warning"
    if cmd.kind == "choice":
        group, value = cmd_id.split(":")
        setattr(m, "view_mode" if group == "view" else "filter_mode", value)
        return f"{'View' if group == 'view' else 'Filter'}: {value}", "info"
    match cmd_id:
        case "pause":
            m.paused = not m.paused
            return ("Paused" if m.paused else "Polling resumed"), "info"
        case "faster":
            return f"Poll rate: {m.faster()}", "info"
        case "slower":
            return f"Poll rate: {m.slower()}", "info"
        case "schedule":
            m.schedule = "per-PID" if m.schedule == "fixed" else "fixed"
            return f"Poll schedule: {m.rate}", "info"
        case "view_next":
            return f"View: {m.cycle_view()}", "info"
        case "filter_next":
            return f"Filter: {m.cycle_filter()}", "info"
        case "rulers":
            m.show_rulers = not m.show_rulers
            return f"Byte ruler {'on' if m.show_rulers else 'off'}", "info"
        case "actuators":
            m.show_actuators = not getattr(m, "show_actuators", False)
            return "Mockup: the actuator pane is not drawn", "info"
        case "deselect":
            m.selected = None
            return "Selection cleared", "info"
        case "new":
            m.segments += 1
            m.segment_started = time.monotonic()
            return "Recording saved → captures/2026-11-12.json. Now recording", "success"
        case "verify":
            return f"{m.selected[2]} verified=true (mockup: not written)", "success"
        case "reconnect":
            return "Reconnecting after this poll cycle…", "info"
    return f"Mockup: “{cmd.label.rstrip('…')}” is not wired", "info"
