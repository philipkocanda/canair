"""Live monitor — what each row of the actuator pane says, as Rich text.

The pane (``tui/apps/monitor/actuators.py``) puts :func:`actuator_cells` into its table, one row
per :class:`~canair.live.monitor.actuation.Actuator`. The colours carry the meanings of the
cli-tui-standards skill, written out in :data:`ACTUATOR_LEGEND` for the help overlay.

The readback column costs no bus traffic: it shows the entry's ``status_param`` signal as the
monitor last decoded it, when its PID is polled.
"""

from __future__ import annotations

from rich.text import Text

from canair.live.actuate.iocontrol_actuate import ACTUATOR_ERROR, ACTUATOR_OFF, ACTUATOR_ON
from canair.live.actuate.routines_execute import sf_label
from canair.render import glyphs
from canair.render.formatting import format_value
from canair.uds.subfunctions import RoutineControlType

from .actuation import Actuator, MonitorActuation
from .controller import MonitorController
from .health import label_ecu

#: The table's column keys and headings, in order.
ACTUATOR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mark", ""),
    ("ecu", "ECU"),
    ("id", "ID"),
    ("label", "Label"),
    ("state", "State"),
    ("readback", "Readback"),
    ("reply", "Last reply"),
)

ACTUATOR_LEGEND: tuple[tuple[str, str, str], ...] = (
    (glyphs.OK, "green", "verified"),
    ("?", "yellow", "unverified"),
    ("ON", "bold green", "switched on this run"),
    ("queued", "dim", "the request waits for the bus"),
    ("started", "bold yellow", "the routine was started"),
    ("NRC", "yellow", "the ECU sent a negative response"),
    ("ERR", "bold red", "the request failed"),
)

NOT_POLLED = "not polled"


def readback(c: MonitorController, ecu: str, signal: str | None) -> str | None:
    """``signal``'s value on ``ecu`` as the monitor last showed it; None when not polled."""
    if not signal:
        return None
    for label, entries in c.last_queries:
        if label_ecu(label) != ecu:
            continue
        for entry in entries:
            for row in entry.get("params") or ():
                if row[0] == signal:
                    _name, value, unit, _expr, error, _verified, display = row
                    return error if error else format_value(value, unit, display)
    return None


def _io_state(a: MonitorActuation, act: Actuator) -> Text:
    if act.key in a.pending:
        return Text(f"queued{glyphs.ELLIPSIS}", style="dim")
    state = a.io_bank(act).state.get(act.ident)
    if state == ACTUATOR_ON:
        return Text("ON", style="bold green")
    if state == ACTUATOR_OFF:
        return Text("OFF", style="dim")
    if state == ACTUATOR_ERROR:
        return Text("ERR", style="bold red")
    return Text(glyphs.EMPTY, style="dim")


def _routine_state(a: MonitorActuation, act: Actuator) -> Text:
    if act.key in a.pending:
        return Text(f"queued{glyphs.ELLIPSIS}", style="dim")
    bank = a.routine_bank(act)
    state, sf = bank.state.get(act.ident), bank.last_sf.get(act.ident)
    if state == "nrc":
        return Text("NRC", style="yellow")
    if state == "error":
        return Text("ERR", style="bold red")
    if state == "ok" and sf == RoutineControlType.START_ROUTINE:
        return Text("started", style="bold yellow")
    if state == "ok" and sf is not None:
        return Text(sf_label(sf), style="green")
    return Text(glyphs.EMPTY, style="dim")


def actuator_cells(c: MonitorController, act: Actuator) -> tuple[Text, ...]:
    """The cells of one row, in :data:`ACTUATOR_COLUMNS` order."""
    a = c.actuation
    assert a is not None  # the pane exists only with --actuate
    mark = Text(glyphs.OK, style="green") if act.verified else Text("?", style="yellow")
    if act.kind == "iocontrol":
        state = _io_state(a, act)
        reply = a.io_bank(act).last_response.get(act.ident, "")
        if act.status_param:
            value = readback(c, act.ecu, act.status_param)
            shown = Text(value) if value is not None else Text(NOT_POLLED, style="dim")
        else:
            shown = Text(glyphs.EMPTY, style="dim")
    else:
        state = _routine_state(a, act)
        reply = a.routine_bank(act).last_response.get(act.ident, "")
        shown = Text(glyphs.EMPTY, style="dim")
    return (
        mark,
        Text(act.ecu, style="cyan"),
        Text(act.ident, style="cyan"),
        Text(act.label),
        state,
        shown,
        Text(reply, style="dim"),
    )


def pane_title(c: MonitorController) -> Text:
    """The pane's top line: what it drives, what is ON, and the request on the bus."""
    a = c.actuation
    assert a is not None
    sep = f"  {glyphs.SEP}  "
    out = Text()
    out.append("Actuators ", style="bold")
    out.append(", ".join(dict.fromkeys([*a.io, *a.routines])), style="bold cyan")
    on = a.on_count()
    if on:
        out.append(sep, style="dim")
        out.append(f"{glyphs.WARN} {on} ON", style="yellow")
    current = c.foreground.current
    if current:
        out.append(sep, style="dim")
        out.append(f"sending {current}{glyphs.ELLIPSIS}", style="dim")
    return out
