"""Routines mode — the interactive RoutineControl view of ``canair routines ECU``.

Each entry in the ``routines:`` YAML section is a Routine Identifier (RID)
discovered by ``canair scan routines``. Three sub-modes serve it:

* ``routines_list.mode_routines_list``   — offline listing of all RIDs for an ECU.
* ``routines_execute.mode_routines_execute`` — send a single sub-function to a specific RID.
* :func:`mode_routines_tui` — the interactive view (this module).

Sub-functions (UDS 0x31):
    0x01  startRoutine       — DANGEROUS: starts a routine; the view confirms first
    0x02  stopRoutine        — stops a running routine
    0x03  requestRoutineResults — safe read-only: "what happened last time?"

:class:`RoutinesController` holds what the view shows and does: the diagnostic session it owns
and the edits to ``ecus/<ecu>.yaml``. Its :class:`~.routine_bank.RoutineBank` holds the result
of the last request per RID and sends the requests; :func:`routine_cells`
says what each row shows, in the colours of the cli-tui-standards skill. The screen is a Textual
app (``tui/apps/routines/``) that the CLI passes in as ``interactive``, because nothing below
``tui/`` imports Textual. The view sends 0x03 by default; startRoutine needs its own key and a
confirmation, keeping read-only and write operations apart. A KWP2000 ECU is refused
(:mod:`.routines_execute`): there, 0x31 starts a routine.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from rich.table import Table
from rich.text import Text

from canair.core.paths import config_dir
from canair.core.terminal import is_interactive
from canair.ecus.pids import RoutineIndexEntry, build_routines_index, load_pids
from canair.live.actuate.not_found import report_not_found
from canair.live.actuate.ports import OwnSessions, StatusLevel
from canair.live.actuate.routine_bank import RoutineBank, RoutineState
from canair.live.actuate.routines_execute import check_routines_protocol, sf_label
from canair.profile.model import Profile
from canair.render import glyphs, messages
from canair.render.console import console
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.subfunctions import RoutineControlType
from canair.yaml_edit.pids import PidsEditError, update_routines_field

_tui_logger = logging.getLogger("routines-tui")


def _log_file() -> Path:
    """The TUI debug log, cleared each session, next to canair's other logs."""
    return config_dir() / "logs" / "routines-tui.log"


class RoutinesController:
    """The state and the operations of the routines view, with no terminal I/O."""

    def __init__(
        self,
        terminal: Terminal,
        ecu_key: str,
        ecu_info: RoutineIndexEntry,
        pids_data: dict,
        profile: Profile,
        verbose: bool = False,
    ):
        self.profile = profile
        self.terminal = terminal
        self.ecu_key = ecu_key
        self.tx_id = ecu_info["tx_id"]
        self.pids_data = pids_data
        self.verbose = verbose
        self.stopping = False  # set once the user quits
        # The latest message for the user; the view shows it and clears it.
        self.status = ""
        self.status_level: StatusLevel = "info"
        self.sm = SessionManager(terminal, verbose=verbose)
        self.sessions = OwnSessions(self.sm)
        self.bank = RoutineBank(
            lambda: self.terminal, self.tx_id, ecu_info["routines"], self.sessions, self.report
        )

    # -- what the bank holds, as the view reads it --------------------------
    @property
    def routines(self) -> dict[str, dict]:
        return self.bank.routines

    @property
    def rids(self) -> list[str]:
        return list(self.bank.routines)

    @property
    def state(self) -> dict[str, RoutineState | None]:
        return self.bank.state

    @property
    def last_response(self) -> dict[str, str]:
        return self.bank.last_response

    @property
    def last_sf(self) -> dict[str, int]:
        return self.bank.last_sf

    @property
    def busy(self) -> bool:
        return self.bank.busy

    @property
    def session_active(self) -> bool:
        return self.sessions.active

    # -- messages ----------------------------------------------------------
    def report(self, text: str, level: StatusLevel = "info") -> None:
        self.status = text
        self.status_level = level

    def take_status(self) -> tuple[str, StatusLevel] | None:
        """The pending message, once: the view shows it and it is gone."""
        if not self.status:
            return None
        out = (self.status, self.status_level)
        self.report("")
        return out

    # -- requests ----------------------------------------------------------
    async def send(self, rid: str, sub_function: int) -> None:
        """Send ``31 {SF} {RID_HI} {RID_LO}`` and record the outcome."""
        await self.bank.send(rid, sub_function)

    async def cleanup(self) -> None:
        await self.sessions.close()

    # -- edits to ecus/ ----------------------------------------------------
    def apply_edit(self, rid: str, field: str, value: str | bool) -> None:
        """Write one field of ``rid`` to ecus/<ecu>.yaml and reload; report the result."""
        try:
            fpath = update_routines_field(self.ecu_key, rid, field, value, profile=self.profile)
        except PidsEditError as exc:
            self.report(f"Edit failed: {exc}", "error")
            _tui_logger.error("edit %s %s=%r: %s", rid, field, value, exc)
            return
        try:
            fresh = load_pids(profile=self.profile)
            self.pids_data.clear()
            self.pids_data.update(fresh)
            idx = build_routines_index(self.pids_data)
            self.bank.set_routines(idx[self.ecu_key]["routines"])
        except Exception as exc:
            _tui_logger.warning("reload after edit failed: %s", exc)
        self.report(f"Saved {field} on {rid} {glyphs.ARROW} {fpath.name}", "success")
        _tui_logger.info("edit %s %s=%r -> %s", rid, field, value, fpath)


# -- what the view shows ---------------------------------------------------
# The Textual app puts routine_cells() into its table, and routines_table() prints the same rows
# on the main screen when the view closes.

#: The table's column keys and headings, in order.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("mark", ""),
    ("rid", "RID"),
    ("label", "Label"),
    ("result", "Res"),
    ("subfn", "Sub-fn"),
    ("response", "Last response"),
)

ROUTINES_LEGEND: tuple[tuple[str, str, str], ...] = (
    (glyphs.OK, "green", "verified"),
    ("?", "yellow", "unverified"),
    ("OK", "bold green", "the ECU answered positively"),
    ("NRC", "yellow", "the ECU sent a negative response"),
    ("ERR", "bold red", "the request failed"),
    ("startRoutine", "bold yellow", "the last request started the routine"),
)


def routines_header(c: RoutinesController) -> Text:
    """The header: the ECU, the diagnostic session and the number of RIDs."""
    sep = f"  {glyphs.SEP}  "
    out = Text()
    out.append("Routines ", style="bold")
    out.append(c.ecu_key, style="bold cyan")
    out.append(f" (0x{c.tx_id:03X})", style="dim")
    out.append(sep, style="dim")
    out.append("session ", style="dim")
    if c.session_active:
        out.append("active", style="green")
    else:
        out.append("not started", style="dim")
    out.append(sep, style="dim")
    out.append(f"{len(c.rids)} RIDs", style="dim")
    return out


def _result(state: str | None) -> Text:
    if state == "ok":
        return Text("OK", style="bold green")
    if state == "nrc":
        return Text("NRC", style="yellow")
    if state == "error":
        return Text("ERR", style="bold red")
    return Text(glyphs.SEP, style="dim")


def _subfn(sf: int | None) -> Text:
    if sf is None:
        return Text("")
    if sf == RoutineControlType.START_ROUTINE:
        return Text(sf_label(sf), style="bold yellow")
    if sf == RoutineControlType.REQUEST_ROUTINE_RESULTS:
        return Text(sf_label(sf), style="dim")
    return Text(sf_label(sf))


def routine_cells(c: RoutinesController, rid: str) -> tuple[Text, ...]:
    """The cells of one RID's row, in :data:`COLUMNS` order."""
    rdef = c.routines[rid]
    mark = Text(glyphs.OK, style="green") if rdef.get("verified") else Text("?", style="yellow")
    return (
        mark,
        Text(rid, style="cyan"),
        Text(rdef.get("label") or rid),
        _result(c.state.get(rid)),
        _subfn(c.last_sf.get(rid)),
        Text(c.last_response.get(rid, ""), style="dim"),
    )


def routines_table(c: RoutinesController) -> Table:
    """Every RID's row as a Rich table, for the main screen."""
    table = Table(box=None, pad_edge=False, header_style="dim")
    for _key, heading in COLUMNS:
        table.add_column(heading, no_wrap=True)
    for rid in c.rids:
        table.add_row(*routine_cells(c, rid))
    return table


#: Runs the interactive view on a controller and returns when the user quits.
InteractiveView = Callable[[RoutinesController], Awaitable[None]]


def _start_log() -> Path:
    """Attach the per-session debug log (cleared each session)."""
    log_file = _log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    _tui_logger.addHandler(handler)
    _tui_logger.setLevel(logging.DEBUG)
    return log_file


async def run_routines_view(controller: RoutinesController, interactive: InteractiveView) -> None:
    """Run the view, then close the session and print the final table."""
    c = controller
    if not c.rids:
        messages.fail(f"no routines available for {c.ecu_key}")
    if not is_interactive():
        messages.fail(
            "the routines view needs a terminal; use `canair routines ECU --json` to list "
            "RIDs or `canair routines ECU --rid RID` to send one",
            code=2,
        )
    log_file = _start_log()
    _tui_logger.info("TUI session start — %s (0x%03X), %d RIDs", c.ecu_key, c.tx_id, len(c.rids))
    try:
        await interactive(c)
    finally:
        await c.cleanup()
        out = console()
        out.print(routines_header(c))
        out.print(routines_table(c))
        _tui_logger.info("TUI session end")
        messages.note(f"debug log: {log_file}")


async def mode_routines_tui(
    terminal: Terminal,
    pids_data: dict,
    ecu_name: str,
    profile: Profile,
    verbose: bool = False,
    *,
    interactive: InteractiveView,
) -> None:
    """Interactive routines view for an ECU."""
    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in rindex:
        report_not_found(
            f"no routines defined for ECU: {ecu_name}", rindex.keys(), "ECUs with routines"
        )
        raise SystemExit(1)
    check_routines_protocol(pids_data, ecu_key)

    controller = RoutinesController(terminal, ecu_key, rindex[ecu_key], pids_data, profile, verbose)
    await run_routines_view(controller, interactive)
