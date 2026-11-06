"""IOControl view — what each row and the header say, as Rich text.

The Textual app (``tui/apps/iocontrol/``) puts :func:`row_cells` into its table, and
:func:`iocontrol_table` prints the same rows on the main screen when the view closes. The
colours carry the meanings of the cli-tui-standards skill; :data:`IOCONTROL_LEGEND` is the
legend the app's help shows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

from canair.ecus.pids import IoControlCommand
from canair.render import glyphs

from .iocontrol_actuate import ACTUATOR_ERROR, ACTUATOR_OFF, ACTUATOR_ON

if TYPE_CHECKING:
    from .iocontrol import IOControlController

#: The table's column keys and headings, in order.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("mark", ""),
    ("did", "DID"),
    ("label", "Label"),
    ("cmd", "Cmd"),
    ("status", "Status"),
    ("response", "Last response"),
)

IOCONTROL_LEGEND: tuple[tuple[str, str, str], ...] = (
    (glyphs.OK, "green", "curated, verified"),
    ("?", "yellow", "curated, unverified"),
    ("»", "dim", "found by a scan, not yet promoted"),
    ("ON", "bold green", "switched on this session"),
    ("ERR", "bold red", "the last request failed"),
    ("NRC", "yellow", "the ECU sent a negative response"),
)


def session_text(c: IOControlController) -> Text:
    """The header: the ECU, the diagnostic session, the view mode and the poll."""
    sep = f"  {glyphs.SEP}  "
    out = Text()
    out.append("IOControl ", style="bold")
    out.append(c.ecu_key, style="bold cyan")
    out.append(f" (0x{c.tx_id:03X})", style="dim")
    out.append(sep, style="dim")
    out.append("session ", style="dim")
    if c.session_active:
        out.append(f"active (10 {c.session_mode})", style="green")
    elif c.session_mode:
        out.append(f"unconfirmed (10 {c.session_mode})", style="yellow")
    else:
        out.append("not started", style="dim")
    out.append(sep, style="dim")
    out.append("view ", style="dim")
    curated, disc = c.counts()
    if c.view_mode == "curated":
        out.append("curated", style="bold")
        out.append(f" ({curated})")
    elif c.view_mode == "discoveries":
        out.append("discoveries", style="yellow")
        out.append(f" ({disc} to triage)")
    else:
        out.append("all", style="bold")
        out.append(f" ({curated}+{disc})")
    if c.polling:
        out.append(f"{sep}polling", style="dim")
    return out


def _mark(cmd: IoControlCommand) -> Text:
    if cmd.get("discovery"):
        return Text("»", style="dim")
    if cmd.get("verified"):
        return Text(glyphs.OK, style="green")
    return Text("?", style="yellow")


def _cmd(state: str | None) -> Text:
    if state == ACTUATOR_ON:
        return Text("ON", style="bold green")
    if state == ACTUATOR_OFF:
        return Text("OFF", style="dim")
    if state == ACTUATOR_ERROR:
        return Text("ERR", style="bold red")
    return Text(glyphs.SEP, style="dim")


def _status(sb: str | None) -> Text:
    # None = no response seen yet; "" = a positive response with no tail bytes.
    if sb is None:
        return Text(glyphs.EMPTY, style="dim")
    if sb == "":
        return Text(glyphs.SEP, style="dim")
    if sb.startswith("ERR"):
        return Text(sb, style="red")
    if sb.startswith("NRC"):
        return Text(sb, style="yellow")
    return Text(sb)


def row_cells(c: IOControlController, did: str) -> tuple[Text, ...]:
    """The cells of one DID's row, in :data:`COLUMNS` order."""
    cmd = c.all_cmds[did]
    return (
        _mark(cmd),
        Text(did, style="cyan"),
        Text(cmd["label"] or did),
        _cmd(c.state.get(did)),
        _status(c.status_bytes.get(did)),
        Text(c.last_response.get(did, ""), style="dim"),
    )


def iocontrol_table(c: IOControlController) -> Table:
    """The rows of the current view as a Rich table, for the main screen."""
    table = Table(box=None, pad_edge=False, header_style="dim")
    for _key, heading in COLUMNS:
        table.add_column(heading, no_wrap=True)
    for did in c.dids:
        table.add_row(*row_cells(c, did))
    return table
