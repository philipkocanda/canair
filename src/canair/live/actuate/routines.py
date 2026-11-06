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

:class:`RoutinesController` holds what the view shows and does: the result of the last request
per RID, the diagnostic session, and the edits to ``ecus/<ecu>.yaml``; :func:`routine_cells`
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
from typing import Literal

from rich.table import Table
from rich.text import Text

from canair.core.paths import config_dir
from canair.core.terminal import is_interactive
from canair.ecus.pids import RoutineIndexEntry, build_routines_index, load_pids
from canair.live.actuate.not_found import report_not_found
from canair.live.actuate.routines_execute import check_routines_protocol, send_routine, sf_label
from canair.profile.model import Profile
from canair.render import glyphs, messages
from canair.render.console import console
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.nrc import nrc_abbrev
from canair.uds.requests import routine_control_request
from canair.uds.subfunctions import RoutineControlType
from canair.yaml_edit.pids import PidsEditError, update_routines_field

_tui_logger = logging.getLogger("routines-tui")

#: How a status message reads: the view colours it and gives it a glyph.
StatusLevel = Literal["error", "warning", "success", "info"]

#: The outcome of the last request to a RID: a positive reply, an NRC, or a failure.
RoutineState = Literal["ok", "nrc", "error"]


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
        # {RID: {label, nrc, nrc_desc, response, verified, notes}}
        self.routines: dict[str, dict] = ecu_info["routines"]
        self.rids = list(self.routines)
        self.pids_data = pids_data
        self.verbose = verbose

        self.state: dict[str, RoutineState | None] = dict.fromkeys(self.rids)
        self.last_response: dict[str, str] = {}
        self.last_sf: dict[str, int] = {}  # which sub-function was last sent

        self.session_active = False
        self.sm = SessionManager(terminal, verbose=verbose)
        self.busy = False
        self.stopping = False  # set once the user quits
        # The latest message for the user; the view shows it and clears it.
        self.status = ""
        self.status_level: StatusLevel = "info"

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
    async def _ensure_session(self) -> None:
        if self.session_active:
            return
        _tui_logger.info("Opening extended session (10 03) on 0x%03X", self.tx_id)
        outcome = await self.sm.open_session(self.tx_id, force=True, report=False)
        self.sm.start_background_keepalive()
        self.session_active = outcome.ok
        _tui_logger.info("Session established: %s (%s)", outcome.ok, outcome.problem() or "ok")

    async def send(self, rid: str, sub_function: int) -> None:
        """Send ``31 {SF} {RID_HI} {RID_LO}`` and record the outcome."""
        hex_cmd = routine_control_request(sub_function, int(rid, 16))
        self.busy = True
        self.report(f"Sending {sf_label(sub_function)}: {rid}{glyphs.ELLIPSIS}")
        _tui_logger.info("SF %s %s cmd=%s", sf_label(sub_function), rid, hex_cmd)
        try:
            await self._ensure_session()
            await self.terminal.set_header(self.tx_id)
            resp = await send_routine(self.terminal, self.tx_id, hex_cmd)
            _tui_logger.info("SF %s resp: %s", rid, resp)
            self.last_sf[rid] = sub_function
            if resp["ok"]:
                self.state[rid] = "ok"
                self.last_response[rid] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[rid] = "nrc"
                protocol = self.terminal.session_policy.protocol(self.tx_id)
                self.last_response[rid] = (
                    f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'], protocol)}"
                )
            else:
                self.state[rid] = "error"
                self.last_response[rid] = resp.get("error", "unknown error")
            self.report("")
        except Exception as e:
            _tui_logger.error("SF %s %s exception: %s", rid, sub_function, e, exc_info=True)
            self.state[rid] = "error"
            self.last_response[rid] = str(e)
            self.report("")
        finally:
            self.busy = False

    async def cleanup(self) -> None:
        await self.sm.stop_background_keepalive()

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
            self.routines = idx[self.ecu_key]["routines"]
            self.rids = list(self.routines)
            for r in self.rids:
                self.state.setdefault(r, None)
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
