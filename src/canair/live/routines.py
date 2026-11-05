"""Routines mode — list, execute, and interactive TUI for RoutineControl commands.

Each entry in the ``routines:`` YAML section is a Routine Identifier (RID)
discovered by ``canair scan routines``. This mode exposes three sub-modes:

* ``routines_list.mode_routines_list``   — offline listing of all RIDs for an ECU.
* ``routines_execute.mode_routines_execute`` — send a single sub-function to a specific RID.
* ``mode_routines_tui``   — interactive TUI for querying / starting routines (this module).

Sub-functions (UDS 0x31):
    0x01  startRoutine       — DANGEROUS: starts a routine; requires explicit
                               --start flag (never sent by default)
    0x02  stopRoutine        — stops a running routine
    0x03  requestRoutineResults — safe read-only: "what happened last time?"

The TUI always defaults to 0x03 (requestRoutineResults). The user must press
'!' (exclamation mark) to send 0x01 startRoutine after an explicit prompt —
matching the UDS philosophy of keeping read-only and write operations separate.
"""

import asyncio
import logging
import os
import sys
import termios
import tty
from pathlib import Path

from canair.core.paths import config_dir
from canair.core.terminal import is_interactive, truncate_text
from canair.core.terminal import terminal_columns as _terminal_columns
from canair.core.terminal import terminal_lines as _terminal_lines
from canair.ecus.pids import RoutineIndexEntry, build_routines_index, load_pids
from canair.live._iocontrol_render import CARET
from canair.live._not_found import report_not_found
from canair.live.routines_execute import sf_label
from canair.render import ansi, glyphs, messages
from canair.transport.protocol import Terminal
from canair.uds.nrc import nrc_abbrev
from canair.uds.requests import routine_control_request
from canair.uds.subfunctions import RoutineControlType
from canair.yaml_edit.pids import PidsEditError, update_routines_field

_tui_logger = logging.getLogger("routines-tui")


def _log_file() -> Path:
    """The TUI debug log, cleared each session, next to canair's other logs."""
    return config_dir() / "logs" / "routines-tui.log"


# ── TUI ───────────────────────────────────────────────────────────────────────


class _RoutinesTUI:
    """Interactive Routines TUI — navigate RIDs and send sub-functions.

    Keys:
        ↑/↓ or j/k   Navigate
        PgUp/Dn       Scroll page
        g/G           Jump to top/bottom
        Enter/Space   Send requestRoutineResults (SF 0x03 — safe, read-only)
        !             Send startRoutine (SF 0x01 — requires explicit confirmation)
        s             Send stopRoutine (SF 0x02)
        e             Edit label
        n             Edit notes
        m             Toggle verified
        q / Ctrl+C    Quit
    """

    def __init__(
        self,
        terminal: Terminal,
        ecu_key: str,
        ecu_info: RoutineIndexEntry,
        pids_data: dict,
        verbose: bool = False,
    ):
        self.terminal = terminal
        self.ecu_key = ecu_key
        self.tx_id = ecu_info["tx_id"]
        self.routines = ecu_info[
            "routines"
        ]  # {RID: {label, nrc, nrc_desc, response, verified, notes}}
        self.rids = list(self.routines.keys())
        self.pids_data = pids_data
        self.verbose = verbose

        self.cursor = 0

        # Per-RID state: None=idle, "ok"=positive, "nrc"=NRC, "error"=failed
        self.state: dict[str, str | None] = dict.fromkeys(self.rids)
        self.last_response: dict[str, str] = {}
        self.last_sf: dict[str, int] = {}  # which sub-function was last sent

        self._session_active = False
        self._tester_task: asyncio.Task | None = None
        self._quit = False
        self._busy = False
        self._status = ""
        self._confirm_start: str | None = None  # RID awaiting start confirmation, or None
        self._confirm_buf = ""  # what is typed at the startRoutine confirmation
        self._edit_input: tuple[str, str] | None = None

        self._scroll_top = 0

    def _render(self) -> str:
        lines = []
        lines.append(
            ansi.c(
                f"  Routines TUI {glyphs.EMPTY} {self.ecu_key} (0x{self.tx_id:03X})",
                ansi.BOLD + ansi.CYAN,
            )
        )
        if self._session_active:
            sess = ansi.c("active", ansi.GREEN)
        else:
            sess = ansi.c("not started", ansi.DIM)
        rid_count = ansi.c(f"{len(self.rids)} RIDs", ansi.DIM)
        lines.append(f"{ansi.c('  Session: ', ansi.DIM)}{sess}   {rid_count}")
        lines.append("")

        term_w = _terminal_columns()
        rid_w = max((len(r) for r in self.rids), default=4)
        raw_label_w = max((len(self.routines[r]["label"]) for r in self.rids), default=5)
        last_sf_w = max(
            (len(sf_label(self.last_sf[r])) for r in self.rids if r in self.last_sf),
            default=len("Sub-fn"),
        )
        last_sf_w = max(last_sf_w, len("Sub-fn"))

        non_flex = 5 + rid_w + 2 + 3 + 2 + last_sf_w + 2 + 2 + 13
        avail = max(0, term_w - non_flex - 5)
        label_w = min(raw_label_w, max(8, avail))

        total_w = 5 + rid_w + 2 + label_w + 2 + 3 + 2 + last_sf_w + 2 + 13
        ruler = "─" * max(1, total_w - 2)

        hdr = (
            f"     "
            f"{'RID':<{rid_w}}  "
            f"{'Label':<{label_w}}  "
            f"{'Res':<3}  "
            f"{'Sub-fn':<{last_sf_w}}  "
            f"Last response"
        )
        lines.append(ansi.c(hdr, ansi.DIM))
        lines.append(ansi.c(f"  {ruler}", ansi.DIM))

        term_h = _terminal_lines()
        chrome = 5 + 3
        viewport_rows = max(5, term_h - chrome)
        n_rids = len(self.rids)

        if self.cursor < self._scroll_top:
            self._scroll_top = self.cursor
        elif self.cursor >= self._scroll_top + viewport_rows:
            self._scroll_top = self.cursor - viewport_rows + 1
        self._scroll_top = max(0, min(self._scroll_top, max(0, n_rids - viewport_rows)))
        visible_start = self._scroll_top
        visible_end = min(n_rids, self._scroll_top + viewport_rows)

        for i in range(visible_start, visible_end):
            rid = self.rids[i]
            rdef = self.routines[rid]
            is_cursor = i == self.cursor
            state = self.state[rid]
            resp = self.last_response.get(rid, "")
            sf = self.last_sf.get(rid)

            prefix = f" {ansi.c(glyphs.CURSOR, ansi.BOLD)} " if is_cursor else "   "

            if rdef["verified"]:
                v_mark = ansi.c(glyphs.OK, ansi.GREEN) + " "
            else:
                v_mark = ansi.c("?", ansi.YELLOW) + " "

            label_text = truncate_text(rdef["label"] or rid, label_w)
            rid_code = ansi.BOLD + ansi.CYAN if is_cursor else ansi.CYAN
            label_cell = f"{label_text:<{label_w}}"
            if is_cursor:
                label_cell = ansi.c(label_cell, ansi.BOLD)
            rid_label = f"{ansi.c(f'{rid:<{rid_w}}', rid_code)}  {label_cell}"

            # Result column (3 chars)
            if state == "ok":
                res_part = ansi.c("OK ", ansi.BOLD + ansi.GREEN)
            elif state == "nrc":
                res_part = ansi.c("NRC", ansi.YELLOW)
            elif state == "error":
                res_part = ansi.c("ERR", ansi.BOLD + ansi.RED)
            else:
                res_part = ansi.c(f" {glyphs.SEP} ", ansi.DIM)

            # Sub-function column
            sf_text = sf_label(sf) if sf is not None else ""
            if sf == RoutineControlType.START_ROUTINE:
                sf_part = ansi.c(f"{sf_text:<{last_sf_w}}", ansi.BOLD + ansi.YELLOW)
            elif sf == RoutineControlType.REQUEST_ROUTINE_RESULTS:
                sf_part = ansi.c(f"{sf_text:<{last_sf_w}}", ansi.DIM)
            else:
                sf_part = f"{sf_text:<{last_sf_w}}"

            resp_part = "  " + ansi.c(resp, ansi.DIM) if resp else ""

            lines.append(f"{prefix}{v_mark}{rid_label}  {res_part}  {sf_part}{resp_part}")

        # Scroll indicator
        if n_rids > viewport_rows:
            above = visible_start
            below = n_rids - visible_end
            up = "↑" if above else glyphs.SEP
            dn = "↓" if below else glyphs.SEP
            lines.append(
                ansi.c(
                    f"  {up}{dn} {self.cursor + 1}/{n_rids} "
                    f"(viewing {visible_start + 1}–{visible_end}, {above}↑ {below}↓)",
                    ansi.DIM,
                )
            )
        else:
            lines.append("")

        # Confirmation prompt for startRoutine
        if self._confirm_start is not None:
            rid = self._confirm_start
            question = (
                f"{glyphs.DANGER} startRoutine for {rid} {glyphs.EMPTY} "
                "type 'yes' + Enter to confirm, Esc to cancel:"
            )
            lines.append(
                f"  {ansi.c(question, ansi.BOLD + ansi.YELLOW)} {self._confirm_buf}{CARET}"
            )
            lines.append(
                ansi.c(
                    "  startRoutine (0x01) may actuate hardware. "
                    "Only proceed if you know the effect.",
                    ansi.DIM,
                )
            )
        elif self._edit_input is not None:
            rid = self.rids[self.cursor]
            field, buf = self._edit_input
            prompt = ansi.c(f"{field.capitalize()} for {rid}: ", ansi.BOLD)
            lines.append(f"  {prompt}{buf}{CARET}")
            lines.append(
                ansi.c("  Type new value, Enter to save to ecus/*.yaml, Esc to cancel", ansi.DIM)
            )
        elif self._status:
            lines.append(f"  {self._status}")
            lines.append("")
        else:
            lines.append("")
            lines.append("")

        keys = (
            "  ↑↓/jk Nav  PgUp/Dn  g/G Top/Bot  Enter Results(SF03)  s Stop(SF02)  "
            "! Start(SF01)  e Label  n Notes  m Verified  q Quit"
        )
        lines.append(ansi.c(keys, ansi.DIM))
        return "\n".join(lines)

    async def _ensure_session(self):
        if self._session_active:
            return
        _tui_logger.info("Opening extended session (10 03) on 0x%03X", self.tx_id)
        await self.terminal.set_header(self.tx_id)
        ok, self._tester_task = await self.terminal.enter_extended_session()
        self._session_active = ok
        _tui_logger.info("Session established: %s", ok)

    async def _send_sf(self, rid: str, sub_function: int):
        """Send ``31 {SF} {RID_HI} {RID_LO}`` and update state."""
        hex_cmd = routine_control_request(sub_function, int(rid, 16))
        self._busy = True
        self._status = f"Sending {sf_label(sub_function)}: {rid}{glyphs.ELLIPSIS}"
        _tui_logger.info("SF %s %s cmd=%s", sf_label(sub_function), rid, hex_cmd)
        try:
            await self._ensure_session()
            await self.terminal.set_header(self.tx_id)
            resp = await self.terminal.send_uds(hex_cmd, timeout=3.0)
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
            self._status = ""
        except Exception as e:
            _tui_logger.error("SF %s %s exception: %s", rid, sub_function, e, exc_info=True)
            self.state[rid] = "error"
            self.last_response[rid] = str(e)
            self._status = ""
        finally:
            self._busy = False

    def _apply_edit(self, rid: str, field: str, value) -> None:
        try:
            fpath = update_routines_field(self.ecu_key, rid, field, value)
        except PidsEditError as exc:
            self._status = ansi.c(f"{glyphs.FAIL} Edit failed: {exc}", ansi.RED)
            _tui_logger.error("edit %s %s=%r: %s", rid, field, value, exc)
            return

        try:
            fresh = load_pids()
            self.pids_data.clear()
            self.pids_data.update(fresh)
            idx = build_routines_index(self.pids_data)
            self.routines = idx[self.ecu_key]["routines"]
            self.rids = list(self.routines.keys())
            for r in self.rids:
                self.state.setdefault(r, None)
        except Exception as exc:
            _tui_logger.warning("reload after edit failed: %s", exc)

        saved = f"{glyphs.OK} Saved {field} on {rid} {glyphs.ARROW} {fpath.name}"
        self._status = ansi.c(saved, ansi.GREEN)
        _tui_logger.info("edit %s %s=%r -> %s", rid, field, value, fpath)

    def _draw(self):
        sys.stdout.write(ansi.screen.CLEAR + ansi.screen.HOME)
        sys.stdout.write(self._render())
        sys.stdout.write("\n")
        sys.stdout.flush()

    async def _cleanup(self):
        if self._tester_task:
            self._tester_task.cancel()
            try:
                await self._tester_task
            except asyncio.CancelledError:
                pass

    async def run(self):
        if not self.rids:
            messages.fail(f"no routines available for {self.ecu_key}")
        if not is_interactive():
            messages.fail(
                "the routines view needs a terminal; use `canair routines ECU --json` to list "
                "RIDs or `canair routines ECU --rid RID` to send one",
                code=2,
            )

        log_file = _log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        _tui_logger.addHandler(handler)
        _tui_logger.setLevel(logging.DEBUG)
        _tui_logger.info(
            "TUI session start — %s (0x%03X), %d RIDs", self.ecu_key, self.tx_id, len(self.rids)
        )

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        key_queue: asyncio.Queue[str] = asyncio.Queue()

        def _on_stdin_ready():
            try:
                ch = os.read(fd, 16).decode("utf-8", errors="ignore")
                key_queue.put_nowait(ch)
            except Exception:
                pass

        loop = asyncio.get_event_loop()

        sys.stdout.write(ansi.screen.ALT_SCREEN_ON + ansi.screen.HIDE_CURSOR)
        sys.stdout.flush()

        # Track the start-confirm buffer separately (not via _edit_input)
        self._confirm_buf = ""

        try:
            tty.setcbreak(fd)
            loop.add_reader(fd, _on_stdin_ready)

            self._draw()

            while not self._quit:
                try:
                    key = await asyncio.wait_for(key_queue.get(), timeout=0.5)
                except TimeoutError:
                    self._draw()
                    continue

                if key == "\x03":  # Ctrl+C
                    if self._confirm_start is not None:
                        self._confirm_start = None
                        self._confirm_buf = ""
                    elif self._edit_input is not None:
                        self._edit_input = None
                    else:
                        self._quit = True
                elif key in ("q", "Q") and self._confirm_start is None and self._edit_input is None:
                    self._quit = True

                # ── Start confirmation mode ──────────────────────────────────
                elif self._confirm_start is not None:
                    if key == "\x1b":
                        self._confirm_start = None
                        self._confirm_buf = ""
                        self._status = "startRoutine cancelled."
                    elif key in ("\r", "\n"):
                        if self._confirm_buf.strip().lower() == "yes":
                            rid = self._confirm_start
                            self._confirm_start = None
                            self._confirm_buf = ""
                            if not self._busy:
                                await self._send_sf(rid, RoutineControlType.START_ROUTINE)
                        else:
                            self._confirm_start = None
                            self._confirm_buf = ""
                            self._status = ansi.c(
                                f"startRoutine not confirmed {glyphs.EMPTY} type 'yes' to execute",
                                ansi.YELLOW,
                            )
                    elif key in ("\x7f", "\x08"):
                        self._confirm_buf = self._confirm_buf[:-1]
                    elif len(key) == 1 and key.isprintable():
                        self._confirm_buf += key

                # ── Metadata edit mode ───────────────────────────────────────
                elif self._edit_input is not None:
                    field, buf = self._edit_input
                    if key == "\x1b":
                        self._edit_input = None
                    elif key in ("\r", "\n"):
                        self._edit_input = None
                        rid = self.rids[self.cursor]
                        self._apply_edit(rid, field, buf)
                    elif key in ("\x7f", "\x08"):
                        self._edit_input = (field, buf[:-1])
                    elif len(key) == 1 and (key.isprintable() or key == "\t"):
                        self._edit_input = (field, buf + key)

                # ── Navigation ───────────────────────────────────────────────
                elif key in ("\x1b[A", "k"):
                    self.cursor = max(0, self.cursor - 1)
                elif key in ("\x1b[B", "j"):
                    self.cursor = min(len(self.rids) - 1, self.cursor + 1)
                elif key in ("\x1b[5~", "\x02"):
                    page = max(1, _terminal_lines() - 10)
                    self.cursor = max(0, self.cursor - page)
                elif key in ("\x1b[6~", "\x06"):
                    page = max(1, _terminal_lines() - 10)
                    self.cursor = min(len(self.rids) - 1, self.cursor + page)
                elif key in ("\x1b[H", "g"):
                    self.cursor = 0
                elif key in ("\x1b[F", "G"):
                    self.cursor = len(self.rids) - 1

                # ── Actions ──────────────────────────────────────────────────
                elif key in ("\r", "\n", " "):  # requestRoutineResults (safe)
                    if not self._busy:
                        rid = self.rids[self.cursor]
                        await self._send_sf(rid, RoutineControlType.REQUEST_ROUTINE_RESULTS)
                elif key in ("s", "S"):  # stopRoutine
                    if not self._busy:
                        rid = self.rids[self.cursor]
                        await self._send_sf(rid, RoutineControlType.STOP_ROUTINE)
                elif key == "!":  # startRoutine — requires confirmation
                    if not self._busy:
                        rid = self.rids[self.cursor]
                        self._confirm_start = rid
                        self._confirm_buf = ""

                # ── Metadata edits ───────────────────────────────────────────
                elif key == "e" and not self._busy:
                    rid = self.rids[self.cursor]
                    self._edit_input = ("label", self.routines[rid].get("label", ""))
                elif key == "n" and not self._busy:
                    rid = self.rids[self.cursor]
                    current = (self.routines[rid].get("notes") or "").strip()
                    self._edit_input = ("notes", current)
                elif key == "m" and not self._busy:
                    rid = self.rids[self.cursor]
                    new_val = not bool(self.routines[rid].get("verified", False))
                    self._apply_edit(rid, "verified", new_val)

                self._draw()

        finally:
            loop.remove_reader(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            final_render = self._render()
            sys.stdout.write(ansi.screen.SHOW_CURSOR + ansi.screen.ALT_SCREEN_OFF)
            sys.stdout.flush()
            print(final_render)
            await self._cleanup()
            _tui_logger.info("TUI session end")
            messages.note(f"debug log: {log_file}")


async def mode_routines_tui(
    terminal: Terminal,
    pids_data: dict,
    ecu_name: str,
    verbose: bool = False,
):
    """Interactive Routines TUI for an ECU."""
    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in rindex:
        report_not_found(
            f"no routines defined for ECU: {ecu_name}", rindex.keys(), "ECUs with routines"
        )
        raise SystemExit(1)

    tui = _RoutinesTUI(terminal, ecu_key, rindex[ecu_key], pids_data=pids_data, verbose=verbose)
    await tui.run()
