"""The monitor's menus: every command of the app, where it sits, and the action it runs.

The menu bar, the ``ctrl+p`` palette and the help sheet read this one catalogue
(:mod:`canair.tui.shell.commands`). Each command names an action the app already has, so a
menu item, a palette entry and the key all end in the same method. The menus depend on the run:
while nothing records, the first item saves a snapshot instead of labelling the recording and the
second starts one, and the Actuators menu exists only with ``--actuate``.
"""

from __future__ import annotations

from canair.live.monitor.edit import FILTERS
from canair.live.monitor.view import VIEW_MODES
from canair.tui.shell.commands import SEPARATOR, Catalogue, Command, Menu

VIEW_LABELS = {
    "ecus": "ECUs only",
    "ranges": "Value ranges",
    "signals": "Signals",
    "full": "Signals and bytes",
}
FILTER_LABELS = {
    "all": "All signals",
    "verified": "Verified signals",
    "unverified": "Unverified signals",
    "ship": "Signals that ship",
    "no-ship": "Signals that do not ship",
}

#: The commands that act on the selected signal, and on the focused actuator row.
SIGNAL_COMMANDS = frozenset({"edit", "verify", "ship", "deselect"})
ACTUATOR_COMMANDS = frozenset({"act_toggle", "act_off", "act_set", "routine_start", "routine_stop"})


def _record(recording: bool) -> Menu:
    if recording:
        first = Command(
            "label",
            "Label recording…",
            "save",
            role="session",
            help="Change the label, vehicle states and notes of this recording",
        )
        second = Command(
            "new",
            "Finish and start new…",
            "new_segment",
            role="new",
            help="Write this recording to a capture file and start the next one",
        )
    else:
        first = Command(
            "label",
            "Save snapshot…",
            "save",
            role="session",
            help="Write the payloads captured so far to a capture file (not recording)",
        )
        second = Command(
            "new",
            "Start recording…",
            "new_segment",
            role="new",
            help="Record every poll from now on; it is written to a capture file on quit",
        )
    return Menu(
        "Record",
        (
            first,
            second,
            Command(
                "info",
                "Recording info…",
                "session_info",
                role="info",
                help="Label, states, journal and the recordings of this run",
            ),
            SEPARATOR,
            Command(
                "event_log",
                "Event log…",
                "event_log",
                role="event_log",
                help="This run's messages and its link and decode events",
            ),
            SEPARATOR,
            Command(
                "quit",
                "Quit",
                "quit",
                role="quit",
                help="Save the recording and exit" if recording else "Exit the monitor",
            ),
        ),
        short="Rec",
    )


def _poll() -> Menu:
    return Menu(
        "Poll",
        (
            Command(
                "pause",
                "Pause polling",
                "toggle_pause",
                role="pause",
                kind="toggle",
                help="Stop sending requests; the values hold and the sessions stay open",
            ),
            Command(
                "pick",
                "Select ECUs and PIDs…",
                "pid_picker",
                role="pick",
                help="Change what is polled without a restart",
            ),
            SEPARATOR,
            Command("faster", "Poll faster", "faster", role="increase", help="Poll more often"),
            Command("slower", "Poll slower", "slower", role="decrease", help="Poll less often"),
            Command(
                "schedule",
                "Per-PID schedule",
                "toggle_schedule",
                role="mode",
                kind="toggle",
                help="Poll each PID at its own period instead of all at one interval",
            ),
            SEPARATOR,
            Command(
                "diag_session",
                "Diagnostic session…",
                "session_type",
                role="value_type",
                help="Open a diagnostic session type (10 xx) on one ECU",
            ),
            Command(
                "reconnect",
                "Reconnect",
                "force_reconnect",
                role="reconnect",
                help="Rebuild the connection after this poll cycle",
            ),
        ),
    )


def _view(actuate: bool) -> Menu:
    views = tuple(
        Command(
            f"view:{mode}",
            VIEW_LABELS[mode],
            f"set_view('{mode}')",
            kind="choice",
            help=f"View mode: {mode}",
        )
        for mode in VIEW_MODES
    )
    filters = tuple(
        Command(
            f"filter:{mode}",
            FILTER_LABELS[mode],
            f"set_filter('{mode}')",
            kind="choice",
            help=f"Show {FILTER_LABELS[mode].lower()}",
        )
        for mode in FILTERS
    )
    actuators: tuple[Command, ...] = (
        (
            Command(
                "actuators",
                "Actuator pane",
                "toggle_actuators",
                role="actuators",
                kind="toggle",
                help="The outputs and routines of the --actuate ECUs",
            ),
        )
        if actuate
        else ()
    )
    return Menu(
        "View",
        (
            *views,
            Command("view_next", "Next view mode", "cycle_view", role="view", help="Cycle views"),
            SEPARATOR,
            *filters,
            Command(
                "filter_next",
                "Next filter",
                "cycle_filter",
                role="filter_cycle",
                help="Cycle the signal filters",
            ),
            SEPARATOR,
            Command(
                "rulers",
                "Byte ruler",
                "toggle_rulers",
                role="rulers",
                kind="toggle",
                help="A byte-index ruler, and the bytes each signal reads",
            ),
            *actuators,
        ),
    )


def _signal() -> Menu:
    return Menu(
        "Signal",
        (
            Command(
                "edit",
                "Edit signal…",
                "edit",
                role="edit",
                help="Expression, unit, range, notes, verified, ship",
            ),
            Command(
                "verify",
                "Verified",
                "verify",
                role="verify",
                kind="toggle",
                help="Mark the selected signal verified, or not",
            ),
            Command(
                "ship",
                "Ship to AutoPID",
                "toggle_ship",
                role="exclude",
                kind="toggle",
                help="Include the selected signal in the device's AutoPID profile",
            ),
            SEPARATOR,
            Command(
                "deselect",
                "Deselect",
                "clear_selection",
                role="back",
                help="Drop the signal cursor",
            ),
        ),
        short="Sig",
    )


def _actuators() -> Menu:
    return Menu(
        "Actuators",
        (
            Command(
                "act_toggle",
                "Toggle output",
                "actuator_accept",
                role="accept",
                help="Switch the focused output on or off, or read a routine's results",
            ),
            Command(
                "act_off",
                "Send OFF",
                "actuator_off",
                role="actuate_off",
                help="Hand the focused output back to the ECU",
            ),
            Command(
                "act_set",
                "Set value…",
                "actuator_set_value",
                role="set_value",
                help="Send a value to the focused output",
            ),
            SEPARATOR,
            Command(
                "routine_start",
                "Start routine…",
                "actuator_start",
                role="start_routine",
                help="Start the focused routine (asks first)",
            ),
            Command(
                "routine_stop",
                "Stop routine",
                "actuator_stop",
                role="stop_routine",
                help="Stop the focused routine",
            ),
        ),
        short="Act",
    )


def _help() -> Menu:
    return Menu(
        "Help",
        (
            Command(
                "help",
                "Keyboard shortcuts",
                "help",
                role="help",
                help="Every key, by pane, and what the colours mean",
            ),
            Command(
                "palette",
                "Command palette",
                "command_palette",
                role="palette",
                help="Find any command by name",
            ),
        ),
    )


def monitor_catalogue(*, recording: bool, actuate: bool) -> Catalogue:
    """The monitor's menus, for whether a recording is running and whether ``--actuate`` is on."""
    menus = [_record(recording), _poll(), _view(actuate), _signal()]
    if actuate:
        menus.append(_actuators())
    menus.append(_help())
    return Catalogue(menus)
