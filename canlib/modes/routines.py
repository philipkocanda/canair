"""Routines mode — list, execute, and interactive TUI for RoutineControl commands.

Each entry in the ``routines:`` YAML section is a Routine Identifier (RID)
discovered by ``canair scan routines``. This mode exposes three sub-modes:

* ``mode_routines_list``   — offline listing of all RIDs for an ECU.
* ``mode_routines_execute`` — send a single sub-function to a specific RID.
* ``mode_routines_tui``   — interactive TUI for querying / starting routines.

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
import json
import logging
import os
import sys
import termios
import tty
from pathlib import Path

from ..pids import build_routines_index, load_pids
from ..pids_edit import PidsEditError, update_routines_field
from ..terminal import WiCANTerminal
from ..tui import terminal_columns as _terminal_columns
from ..tui import terminal_lines as _terminal_lines
from ..uds_parse import nrc_abbrev

# TUI debug log — cleared each session
_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
_LOG_FILE = _LOGS_DIR / "routines-tui.log"
_tui_logger = logging.getLogger("routines-tui")

# UDS RoutineControl sub-functions
SF_START = 0x01   # startRoutine — use with care
SF_STOP = 0x02    # stopRoutine
SF_RESULTS = 0x03  # requestRoutineResults — safe, read-only


def _truncate_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _sf_label(sf: int) -> str:
    return {SF_START: "startRoutine", SF_STOP: "stopRoutine", SF_RESULTS: "requestRoutineResults"}.get(
        sf, f"0x{sf:02X}"
    )


# ── Offline list ──────────────────────────────────────────────────────────────


def mode_routines_list(pids_data: dict, ecu_name: str, as_json: bool = False):
    """List all routine RIDs for an ECU (no CAN connection needed)."""
    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in rindex:
        available = sorted(rindex.keys())
        if available:
            print(f"  No routines defined for ECU: {ecu_name}")
            print(f"  ECUs with routines: {', '.join(available)}")
        else:
            print("  No routines defined in any ECU file.")
        return

    ecu_info = rindex[ecu_key]
    routines = ecu_info["routines"]

    if as_json:
        out = {
            "ecu": ecu_key,
            "tx_id": f"0x{ecu_info['tx_id']:03X}",
            "routines": {
                rid: {
                    "label": r["label"],
                    "nrc": f"0x{r['nrc']:02X}" if r.get("nrc") is not None else None,
                    "nrc_desc": r["nrc_desc"],
                    "response": r["response"],
                    "verified": r["verified"],
                    "notes": r["notes"],
                }
                for rid, r in routines.items()
            },
        }
        print(json.dumps(out, indent=2))
        return

    print(f"\n  {ecu_key} -- TX 0x{ecu_info['tx_id']:03X} -- {len(routines)} routine RIDs\n")

    term_w = _terminal_columns()
    rid_w = max(len(r) for r in routines) if routines else 4
    label_w = max((len(r["label"]) for r in routines.values()), default=5)
    label_w = max(label_w, 5)
    scan_w = max((len(r["nrc_desc"] or r["response"] or "") for r in routines.values()), default=10)
    scan_w = max(scan_w, len("Scan result"))

    fixed_w = 2 + rid_w + 2 + label_w + 2 + scan_w + 2 + len("Verified")
    if fixed_w > term_w:
        scan_w = max(10, scan_w - (fixed_w - term_w))

    hdr = (
        f"  {'RID':<{rid_w}}  {'Label':<{label_w}}  {'Scan result':<{scan_w}}  Verified"
    )
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    for rid, r in routines.items():
        v = "✓" if r["verified"] else " "
        if r.get("nrc") is not None:
            scan_result = f"NRC 0x{r['nrc']:02X} {r['nrc_desc'] or ''}"
        elif r["response"]:
            scan_result = f"OK {r['response']}"
        else:
            scan_result = ""
        print(
            f"  {rid:<{rid_w}}  {_truncate_text(r['label'], label_w):<{label_w}}  "
            f"{_truncate_text(scan_result, scan_w):<{scan_w}}     {v}"
        )
    print()


# ── Single-command execute ────────────────────────────────────────────────────


async def mode_routines_execute(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecu_name: str,
    rid: str,
    sub_function: int = SF_RESULTS,
    verbose: bool = False,
    as_json: bool = False,
):
    """Execute a RoutineControl sub-function on a specific RID.

    Defaults to SF_RESULTS (0x03 requestRoutineResults — safe read-only).
    Pass sub_function=SF_START to send startRoutine; the caller is responsible
    for confirming this is intentional.
    """
    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()
    rid_key = rid.upper()

    if ecu_key not in rindex:
        available = sorted(rindex.keys())
        print(f"  No routines for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with routines: {', '.join(available)}")
        return

    ecu_info = rindex[ecu_key]
    routines = ecu_info["routines"]

    if rid_key not in routines:
        available = sorted(routines.keys())
        print(f"  Unknown RID {rid_key} for {ecu_key}")
        if available:
            print(f"  Available RIDs: {', '.join(available)}")
        return

    rdef = routines[rid_key]
    tx_id = ecu_info["tx_id"]
    sf_name = _sf_label(sub_function)
    label = rdef["label"] or rid_key

    hex_cmd = f"31{sub_function:02X}{rid_key}"

    print(f"\n  {ecu_key} 0x{tx_id:03X} -- {label} ({rid_key}) -- {sf_name}")
    print(f"  Command: {hex_cmd}")

    await terminal.set_header(tx_id)
    _, tester_task = await terminal.enter_extended_session()

    try:
        response = await terminal.send_uds(hex_cmd, timeout=3.0)

        if response["ok"]:
            print(f"  ✓ Positive response: {response['hex']}")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            print(f"  ✗ NRC 0x{nrc:02X}: {desc}")
        else:
            error = response.get("error", "unknown")
            print(f"  ✗ Error: {error}")

        if as_json:
            out = {
                "ecu": ecu_key,
                "rid": rid_key,
                "label": label,
                "sub_function": sf_name,
                "command": hex_cmd,
                "ok": response["ok"],
                "response": response["hex"] if response["ok"] else None,
                "nrc": f"0x{response['nrc']:02X}" if response.get("nrc") is not None else None,
            }
            print(json.dumps(out, indent=2))

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

    print()


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
        terminal: WiCANTerminal,
        ecu_key: str,
        ecu_info: dict,
        pids_data: dict,
        verbose: bool = False,
    ):
        self.terminal = terminal
        self.ecu_key = ecu_key
        self.tx_id = ecu_info["tx_id"]
        self.routines = ecu_info["routines"]  # {RID: {label, nrc, nrc_desc, response, verified, notes}}
        self.rids = list(self.routines.keys())
        self.pids_data = pids_data
        self.verbose = verbose

        self.cursor = 0

        # Per-RID state: None=idle, "ok"=positive, "nrc"=NRC, "error"=failed
        self.state: dict[str, str | None] = {r: None for r in self.rids}
        self.last_response: dict[str, str] = {}
        self.last_sf: dict[str, int] = {}  # which sub-function was last sent

        self._session_active = False
        self._tester_task: asyncio.Task | None = None
        self._quit = False
        self._busy = False
        self._status = ""
        self._confirm_start: str | None = None  # RID awaiting start confirmation, or None
        self._edit_input: tuple[str, str] | None = None

        self._scroll_top = 0

    def _render(self) -> str:
        lines = []
        lines.append(f"\033[1;36m  Routines TUI — {self.ecu_key} (0x{self.tx_id:03X})\033[0m")
        sess = "\033[32mactive\033[0m" if self._session_active else "\033[2mnot started\033[0m"
        lines.append(f"\033[2m  Session: \033[0m{sess}   \033[2m{len(self.rids)} RIDs\033[0m")
        lines.append("")

        term_w = _terminal_columns()
        rid_w = max((len(r) for r in self.rids), default=4)
        raw_label_w = max((len(self.routines[r]["label"]) for r in self.rids), default=5)
        last_sf_w = max((len(_sf_label(self.last_sf[r])) for r in self.rids if r in self.last_sf), default=len("Sub-fn"))
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
        lines.append(f"\033[2m{hdr}\033[0m")
        lines.append(f"\033[2m  {ruler}\033[0m")

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

            prefix = " \033[1m▸\033[0m " if is_cursor else "   "

            if rdef["verified"]:
                v_mark = "\033[32m✓\033[0m "
            else:
                v_mark = "\033[33m?\033[0m "

            b0 = "\033[1m" if is_cursor else ""
            b1 = "\033[0m" if is_cursor else ""
            label_text = _truncate_text(rdef["label"] or rid, label_w)
            rid_label = f"{b0}{rid:<{rid_w}}  {label_text:<{label_w}}{b1}"

            # Result column (3 chars)
            if state == "ok":
                res_part = "\033[1;32mOK \033[0m"
            elif state == "nrc":
                res_part = "\033[33mNRC\033[0m"
            elif state == "error":
                res_part = "\033[1;31mERR\033[0m"
            else:
                res_part = "\033[2m · \033[0m"

            # Sub-function column
            sf_text = _sf_label(sf) if sf is not None else ""
            if sf == SF_START:
                sf_part = f"\033[1;31m{sf_text:<{last_sf_w}}\033[0m"
            elif sf == SF_RESULTS:
                sf_part = f"\033[2m{sf_text:<{last_sf_w}}\033[0m"
            else:
                sf_part = f"{sf_text:<{last_sf_w}}"

            resp_part = f"  \033[2m{resp}\033[0m" if resp else ""

            lines.append(
                f"{prefix}{v_mark}{rid_label}  {res_part}  {sf_part}{resp_part}"
            )

        # Scroll indicator
        if n_rids > viewport_rows:
            above = visible_start
            below = n_rids - visible_end
            up = "\033[33m↑\033[0m" if above else "\033[2m·\033[0m"
            dn = "\033[33m↓\033[0m" if below else "\033[2m·\033[0m"
            lines.append(
                f"\033[2m  {up}{dn} {self.cursor + 1}/{n_rids} "
                f"(viewing {visible_start + 1}–{visible_end}, {above}↑ {below}↓)\033[0m"
            )
        else:
            lines.append("")

        # Confirmation prompt for startRoutine
        if self._confirm_start is not None:
            rid = self._confirm_start
            lines.append(
                f"  \033[1;31m!! startRoutine for {rid} — type 'yes' + Enter to confirm, Esc to cancel:\033[0m "
                f"{self._confirm_buf}\033[5m▏\033[0m"
            )
            lines.append(
                "\033[2m  startRoutine (0x01) may actuate hardware. Only proceed if you know the effect.\033[0m"
            )
        elif self._edit_input is not None:
            rid = self.rids[self.cursor]
            field, buf = self._edit_input
            lines.append(
                f"  \033[1;33m{field.capitalize()} for {rid}: \033[0m{buf}\033[5m▏\033[0m"
            )
            lines.append(
                "\033[2m  Type new value, Enter to save to ecus/*.yaml, Esc to cancel\033[0m"
            )
        elif self._status:
            lines.append(f"  {self._status}")
            lines.append("")
        else:
            lines.append("")
            lines.append("")

        lines.append(
            "\033[2m  ↑↓/jk Nav  PgUp/Dn  g/G Top/Bot  Enter Results(SF03)  s Stop(SF02)  "
            "! Start(SF01)  e Label  n Notes  m Verified  q Quit\033[0m"
        )
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
        hex_cmd = f"31{sub_function:02X}{rid}"
        self._busy = True
        self._status = f"Sending {_sf_label(sub_function)}: {rid}..."
        _tui_logger.info("SF %s %s cmd=%s", _sf_label(sub_function), rid, hex_cmd)
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
                self.last_response[rid] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
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
            self._status = f"\033[31mEdit failed: {exc}\033[0m"
            _tui_logger.error("edit %s %s=%r: %s", rid, field, value, exc)
            return

        try:
            fresh = load_pids()
            self.pids_data.clear()
            self.pids_data.update(fresh)
            from ..pids import build_routines_index
            idx = build_routines_index(self.pids_data)
            self.routines = idx[self.ecu_key]["routines"]
            self.rids = list(self.routines.keys())
            for r in self.rids:
                self.state.setdefault(r, None)
        except Exception as exc:
            _tui_logger.warning("reload after edit failed: %s", exc)

        self._status = f"\033[32mSaved {field} on {rid} → {fpath.name}\033[0m"
        _tui_logger.info("edit %s %s=%r -> %s", rid, field, value, fpath)

    def _draw(self):
        sys.stdout.write("\033[2J\033[H")
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
            print("  No routines available.")
            return

        _LOGS_DIR.mkdir(exist_ok=True)
        handler = logging.FileHandler(_LOG_FILE, mode="w")
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

        sys.stdout.write("\033[?1049h")
        sys.stdout.write("\033[?25l")
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
                except (asyncio.TimeoutError, TimeoutError):
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
                                await self._send_sf(rid, SF_START)
                        else:
                            self._confirm_start = None
                            self._confirm_buf = ""
                            self._status = "\033[33mstartRoutine not confirmed — type 'yes' to execute\033[0m"
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
                        await self._send_sf(rid, SF_RESULTS)
                elif key in ("s", "S"):  # stopRoutine
                    if not self._busy:
                        rid = self.rids[self.cursor]
                        await self._send_sf(rid, SF_STOP)
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
            sys.stdout.write("\033[?25h")
            sys.stdout.write("\033[?1049l")
            sys.stdout.flush()
            print(final_render)
            await self._cleanup()
            _tui_logger.info("TUI session end")
            print(f"  Debug log: {_LOG_FILE}")


async def mode_routines_tui(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecu_name: str,
    verbose: bool = False,
):
    """Interactive Routines TUI for an ECU."""
    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in rindex:
        available = sorted(rindex.keys())
        print(f"  No routines defined for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with routines: {', '.join(available)}")
        return

    tui = _RoutinesTUI(
        terminal, ecu_key, rindex[ecu_key], pids_data=pids_data, verbose=verbose
    )
    await tui.run()
