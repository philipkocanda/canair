"""IOControl mode — list, execute, and interactive TUI for IOControl commands.

The TUI mode (--iocontrol ECU with a WiCAN connection) uses alternate screen
buffer + raw ANSI rendering for keyboard-driven actuator toggling.
"""

import asyncio
import json
import logging
import os
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

from ..pids import build_iocontrol_index
from ..terminal import WiCANTerminal
from .status import format_status_value, query_param_status

# TUI debug log — cleared each session, written to logs/iocontrol-tui.log
_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
_LOG_FILE = _LOGS_DIR / "iocontrol-tui.log"
_tui_logger = logging.getLogger("iocontrol-tui")


def mode_iocontrol_list(pids_data: dict, ecu_name: str, as_json: bool = False):
    """List all IOControl DIDs for an ECU (no CAN connection needed)."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        if available:
            print(f"  No IOControl DIDs for ECU: {ecu_name}")
            print(f"  ECUs with IOControl: {', '.join(available)}")
        else:
            print("  No IOControl DIDs defined in any ECU file.")
        return

    ecu_info = ioctrl_index[ecu_key]
    cmds = ecu_info["cmds"]

    if as_json:
        out = {
            "ecu": ecu_key,
            "tx_id": f"0x{ecu_info['tx_id']:03X}",
            "iocontrol": {
                did: {
                    "label": c["label"],
                    "on": c["on"],
                    "off": c["off"],
                    "session": c["session"],
                    "hold": c["hold"],
                    "verified": c["verified"],
                    "status_param": c.get("status_param"),
                }
                for did, c in cmds.items()
            },
        }
        print(json.dumps(out, indent=2))
        return

    print(f"\n  {ecu_key} -- TX 0x{ecu_info['tx_id']:03X} -- {len(cmds)} IOControl DIDs\n")

    # Column-aligned table
    did_w = max(len(d) for d in cmds) if cmds else 4
    label_w = max(len(c["label"]) for c in cmds.values()) if cmds else 5
    on_w = max(len(c["on"]) for c in cmds.values()) if cmds else 2
    off_w = max(len(c["off"]) for c in cmds.values()) if cmds else 3
    sp_w = max((len(c.get("status_param") or "") for c in cmds.values()), default=0)
    sp_w = max(sp_w, len("Status PID"))

    hdr = (
        f"  {'DID':<{did_w}}  {'Label':<{label_w}}  {'ON cmd':<{on_w}}  "
        f"{'OFF cmd':<{off_w}}  {'Status PID':<{sp_w}}  Verified  Hold"
    )
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    for did, c in cmds.items():
        v = "✓" if c["verified"] else " "
        h = "✓" if c["hold"] else " "
        sp = c.get("status_param") or ""
        print(
            f"  {did:<{did_w}}  {c['label']:<{label_w}}  {c['on']:<{on_w}}  {c['off']:<{off_w}}  "
            f"{sp:<{sp_w}}     {v}        {h}"
        )
    print()


async def mode_iocontrol_execute(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecu_name: str,
    did: str,
    off: bool = False,
    verbose: bool = False,
    as_json: bool = False,
):
    """Execute an IOControl ON or OFF command."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()
    did_key = did.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        print(f"  No IOControl DIDs for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with IOControl: {', '.join(available)}")
        return

    ecu_info = ioctrl_index[ecu_key]
    cmds = ecu_info["cmds"]

    if did_key not in cmds:
        available = sorted(cmds.keys())
        print(f"  Unknown DID {did_key} for {ecu_key}")
        if available:
            print(f"  Available DIDs: {', '.join(available)}")
        return

    cmd_def = cmds[did_key]
    tx_id = ecu_info["tx_id"]
    action = "OFF" if off else "ON"
    hex_cmd = cmd_def["off"] if off else cmd_def["on"]
    label = cmd_def["label"]
    needs_session = cmd_def["session"]
    needs_hold = cmd_def["hold"] and not off  # don't hold on OFF
    status_param = cmd_def.get("status_param")

    if not hex_cmd:
        print(f"  No {action} command defined for {ecu_key} {did_key} ({label})")
        return

    print(f"\n  {ecu_key} 0x{tx_id:03X} -- {label} -- {action}")
    print(f"  Command: {hex_cmd}")
    if status_param:
        print(f"  Status param: {status_param}")

    await terminal.set_header(tx_id)

    tester_task = None
    if needs_session:
        if verbose:
            print("  Entering extended diagnostic session (10 03)...")
        _, tester_task = await terminal.enter_extended_session()

    # Query status before executing
    if status_param:
        before = await query_param_status(terminal, pids_data, status_param, verbose=verbose)
        await terminal.set_header(tx_id)  # restore header after status query may have changed it
        if before["error"]:
            print(f"  Status before: ERR {before['error']}")
        else:
            print(f"  Status before: {status_param} = {format_status_value(before)}")

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
                "did": did_key,
                "label": label,
                "action": action.lower(),
                "command": hex_cmd,
                "ok": response["ok"],
                "response": response["hex"] if response["ok"] else None,
                "nrc": f"0x{response['nrc']:02X}" if response.get("nrc") is not None else None,
            }
            print(json.dumps(out, indent=2))

        # Hold session if needed (keep TesterPresent alive until Ctrl+C)
        if needs_hold and response["ok"] and tester_task:
            print("\n  Holding session (Ctrl+C to release)...")
            try:
                await asyncio.Future()  # block forever
            except asyncio.CancelledError:
                pass

    except KeyboardInterrupt:
        print("\n  Releasing...")
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

        # Send OFF command on release if we were holding ON
        if needs_hold and not off and cmd_def["off"]:
            print(f"  Sending OFF: {cmd_def['off']}")
            release_resp = await terminal.send_uds(cmd_def["off"], timeout=3.0)
            if release_resp["ok"]:
                print(f"  ✓ Released: {release_resp['hex']}")
            else:
                error = release_resp.get("error") or f"NRC 0x{release_resp.get('nrc', 0):02X}"
                print(f"  ✗ Release failed: {error}")

        # Query status after command (or after release if we were holding)
        if status_param:
            await terminal.set_header(tx_id)
            after = await query_param_status(terminal, pids_data, status_param, verbose=verbose)
            if after["error"]:
                print(f"  Status after:  ERR {after['error']}")
            else:
                print(f"  Status after:  {status_param} = {format_status_value(after)}")

    print()


# ── TUI ──────────────────────────────────────────────────────────────────────


class _IOControlTUI:
    """Interactive IOControl TUI — navigate DIDs and toggle ON/OFF.

    Keys:
        ↑/↓ or j/k  Navigate
        Enter/Space  Toggle ON (or OFF if already ON)
        o            Send explicit OFF (ReturnControlToECU 00)
        v            Enter hex value → send ShortTermAdjustment (2F{DID}03{hex})
        +/-          Increment/decrement last value sent to current DID
        q / Ctrl+C   Quit (auto-OFF any active actuator)
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
        self.cmds = ecu_info["cmds"]
        self.pids_data = pids_data
        self.verbose = verbose

        # Ordered list of DID keys
        self.dids = list(self.cmds.keys())
        self.cursor = 0

        # Per-DID state: None=idle, "on"=active, "off"=sent off, "error"=failed
        self.state: dict[str, str | None] = {d: None for d in self.dids}
        self.last_response: dict[str, str] = {}  # DID → last response text
        self.last_value: dict[str, bytes] = {}  # DID → last ShortTermAdjustment value bytes

        # Per-DID status: mapped param current value (polled in background)
        # key = DID, value = display string or None
        self.status_val: dict[str, str | None] = {d: None for d in self.dids}
        self._status_polling = False  # True while background poll loop is running

        self._session_active = False
        self._tester_task: asyncio.Task | None = None
        self._quit = False
        self._busy = False  # prevent concurrent CAN commands
        self._status = ""  # bottom status line
        self._hex_input: str | None = None  # None = not in input mode, "" = editing

    def _render(self) -> str:
        """Build the display as a plain string with ANSI codes."""
        lines = []
        lines.append(f"\033[1;36m  IOControl TUI — {self.ecu_key} (0x{self.tx_id:03X})\033[0m")
        sess = "\033[32mactive\033[0m" if self._session_active else "\033[2mnot started\033[0m"
        poll = "  \033[2m[polling status]\033[0m" if self._status_polling else ""
        lines.append(f"\033[2m  Session: \033[0m{sess}{poll}")
        lines.append("")

        # Column widths
        did_w = max(len(d) for d in self.dids) if self.dids else 4
        label_w = max(len(self.cmds[d]["label"]) for d in self.dids) if self.dids else 5
        # Status column: max of mapped param names (or "—" for none)
        status_vals = [
            self.status_val.get(d) or (self.cmds[d].get("status_param") or "")
            for d in self.dids
        ]
        status_hdr = "Status"
        status_w = max(max((len(s) for s in status_vals), default=0), len(status_hdr))

        # Header
        lines.append(
            f"\033[2m     {'DID':<{did_w}}  {'Label':<{label_w}}  State     "
            f"{'Status':<{status_w}}  Response\033[0m"
        )
        lines.append(f"\033[2m     {'─' * (did_w + label_w + status_w + 38)}\033[0m")

        for i, did in enumerate(self.dids):
            cmd = self.cmds[did]
            is_cursor = i == self.cursor
            state = self.state[did]
            resp = self.last_response.get(did, "")
            sv = self.status_val.get(did)
            sp = cmd.get("status_param")

            # Cursor indicator
            prefix = " \033[1m▸\033[0m " if is_cursor else "   "

            # State display
            if state == "on":
                state_part = "\033[1;32m● ON \033[0m"
            elif state == "off":
                state_part = "\033[2m  OFF\033[0m"
            elif state == "error":
                state_part = "\033[1;31m✗ ERR\033[0m"
            else:
                state_part = "\033[2m  ·  \033[0m"

            # Verified marker
            if cmd["verified"]:
                v_part = "\033[32m✓\033[0m"
            else:
                v_part = "\033[33m?\033[0m"

            # Status value display
            if sp is None:
                status_part = f"\033[2m{'—':<{status_w}}\033[0m"
            elif sv is None:
                status_part = f"\033[2m{'…':<{status_w}}\033[0m"
            elif sv.startswith("ERR"):
                status_part = f"\033[31m{sv:<{status_w}}\033[0m"
            else:
                # Highlight 1 (on) in green, 0 (off) dimmed
                if sv.strip() == "1":
                    status_part = f"\033[1;32m{'1':<{status_w}}\033[0m"
                elif sv.strip() == "0":
                    status_part = f"\033[2m{'0':<{status_w}}\033[0m"
                else:
                    status_part = f"{sv:<{status_w}}"

            bold_on = "\033[1m" if is_cursor else ""
            bold_off = "\033[0m" if is_cursor else ""
            resp_part = f"  \033[2m{resp}\033[0m" if resp else ""

            lines.append(
                f"{prefix}{bold_on}{did:<{did_w}}  {cmd['label']:<{label_w}}{bold_off}  "
                f"{state_part}  {v_part} {status_part}{resp_part}"
            )


        lines.append("")
        if self._hex_input is not None:
            did = self.dids[self.cursor]
            lines.append(f"  \033[1;33mValue for {did} (hex): \033[0m{self._hex_input}\033[5m▏\033[0m")
            lines.append("\033[2m  Type hex bytes, Enter to send 2F{DID}03{value}, Esc to cancel\033[0m")
        elif self._status:
            lines.append(f"  {self._status}")
        # Show last value hint for +/- keys
        did = self.dids[self.cursor]
        val_hint = ""
        if did in self.last_value:
            val_hint = f"  \033[2mlast value: {self.last_value[did].hex().upper()}\033[0m"
        lines.append(f"\033[2m  ↑↓/jk Navigate  Enter/Space Toggle  o OFF  v Value  +/- Step  q Quit\033[0m{val_hint}")
        return "\n".join(lines)

    async def _ensure_session(self):
        """Open extended session + TesterPresent if not already active."""
        if self._session_active:
            return
        _tui_logger.info("Opening extended session (10 03) on 0x%03X", self.tx_id)
        await self.terminal.set_header(self.tx_id)
        ok, self._tester_task = await self.terminal.enter_extended_session()
        self._session_active = ok
        _tui_logger.info("Session established: %s", ok)

    async def _send_on(self, did: str):
        """Send ON command for a DID."""
        cmd = self.cmds[did]
        hex_cmd = cmd["on"]
        if not hex_cmd:
            self.state[did] = "error"
            self.last_response[did] = "no ON cmd defined"
            return

        self._busy = True
        self._status = f"Sending ON: {did} ({cmd['label']})..."
        _tui_logger.info("ON  %s %s cmd=%s", did, cmd["label"], hex_cmd)
        try:
            if cmd["session"]:
                await self._ensure_session()
            resp = await self.terminal.send_uds(hex_cmd, timeout=3.0)
            _tui_logger.info("ON  %s resp: %s", did, resp)
            if resp["ok"]:
                self.state[did] = "on"
                self.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[did] = "error"
                self.last_response[did] = f"NRC 0x{resp['nrc']:02X}: {resp['nrc_desc']}"
            else:
                self.state[did] = "error"
                self.last_response[did] = resp.get("error", "unknown error")
            self._status = ""
        except Exception as e:
            _tui_logger.error("ON  %s exception: %s", did, e, exc_info=True)
            self.state[did] = "error"
            self.last_response[did] = str(e)
            self._status = ""
        finally:
            self._busy = False

    async def _send_off(self, did: str):
        """Send OFF command for a DID."""
        cmd = self.cmds[did]
        hex_cmd = cmd["off"]
        if not hex_cmd:
            self.last_response[did] = "no OFF cmd defined"
            return

        self._busy = True
        self._status = f"Sending OFF: {did} ({cmd['label']})..."
        _tui_logger.info("OFF %s %s cmd=%s", did, cmd["label"], hex_cmd)
        try:
            resp = await self.terminal.send_uds(hex_cmd, timeout=3.0)
            _tui_logger.info("OFF %s resp: %s", did, resp)
            if resp["ok"]:
                self.state[did] = "off"
                self.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[did] = "error"
                self.last_response[did] = f"NRC 0x{resp['nrc']:02X}: {resp['nrc_desc']}"
            else:
                self.state[did] = "error"
                self.last_response[did] = resp.get("error", "unknown error")
            self._status = ""
        except Exception as e:
            _tui_logger.error("OFF %s exception: %s", did, e, exc_info=True)
            self.state[did] = "error"
            self.last_response[did] = str(e)
            self._status = ""
        finally:
            self._busy = False

    async def _toggle(self, did: str):
        """Toggle: if ON → OFF, otherwise → ON."""
        if self.state[did] == "on":
            await self._send_off(did)
        else:
            await self._send_on(did)

    async def _send_adjust(self, did: str, value_bytes: bytes):
        """Send ShortTermAdjustment (2F{DID}03{value}) for a DID."""
        did_hex = did.upper()
        hex_cmd = f"2F{did_hex}03{value_bytes.hex().upper()}"

        self._busy = True
        self._status = f"Adjust: {did} → {hex_cmd}"
        _tui_logger.info("ADJ %s cmd=%s", did, hex_cmd)
        try:
            cmd = self.cmds.get(did, {})
            if cmd.get("session", True):
                await self._ensure_session()
            resp = await self.terminal.send_uds(hex_cmd, timeout=3.0)
            _tui_logger.info("ADJ %s resp: %s", did, resp)
            if resp["ok"]:
                self.state[did] = "on"
                self.last_response[did] = resp["hex"]
                self.last_value[did] = value_bytes
            elif resp.get("nrc") is not None:
                self.state[did] = "error"
                self.last_response[did] = f"NRC 0x{resp['nrc']:02X}: {resp['nrc_desc']}"
                self.last_value[did] = value_bytes  # still store for +/- stepping
            else:
                self.state[did] = "error"
                self.last_response[did] = resp.get("error", "unknown error")
            self._status = ""
        except Exception as e:
            _tui_logger.error("ADJ %s exception: %s", did, e, exc_info=True)
            self.state[did] = "error"
            self.last_response[did] = str(e)
            self._status = ""
        finally:
            self._busy = False

    async def _poll_status_once(self):
        """Query all mapped status_param PIDs once and update status_val."""
        from ..pids import build_param_index

        param_index = build_param_index(self.pids_data)

        # Build per-DID query list: DID → param_name (skip DIDs with no status_param)
        did_params = {
            did: self.cmds[did]["status_param"]
            for did in self.dids
            if self.cmds[did].get("status_param")
        }
        if not did_params:
            return

        # Deduplicate: group DIDs that share the same (tx_id, pid) so we only
        # send one UDS request per PID and evaluate expressions for all DIDs
        # that reference params from it.
        from ..elm327 import elm_hex_to_wican_bytes
        from ..expression import evaluate_expression

        pid_to_dids: dict[tuple, list] = {}  # (tx_id, pid) → list of (did, param_name)
        for did, pname in did_params.items():
            key = pname.upper()
            if key not in param_index:
                self.status_val[did] = "?"
                continue
            pinfo = param_index[key]
            pid_key = (pinfo["tx_id"], pinfo["pid"])
            pid_to_dids.setdefault(pid_key, []).append((did, pname, pinfo))

        for (tx_id, pid), did_list in pid_to_dids.items():
            if self._quit or self._busy:
                return
            try:
                await self.terminal.set_header(tx_id)
                response = await self.terminal.send_uds(pid, timeout=3.0)
            except Exception as exc:
                for did, pname, _ in did_list:
                    self.status_val[did] = f"ERR: {exc}"
                continue

            if not response["ok"]:
                nrc = response.get("nrc")
                if nrc is not None:
                    err_str = f"NRC {nrc:02X}"
                else:
                    err_str = response.get("error", "?")
                for did, pname, _ in did_list:
                    self.status_val[did] = f"ERR: {err_str}"
                continue

            try:
                wican_bytes = elm_hex_to_wican_bytes(response["hex"])
            except Exception as exc:
                for did, pname, _ in did_list:
                    self.status_val[did] = f"ERR: {exc}"
                continue

            for did, pname, pinfo in did_list:
                try:
                    val = evaluate_expression(pinfo["expression"], wican_bytes)
                    val = round(val * 100) / 100
                    unit = pinfo.get("unit", "")
                    result = {"param": pname, "value": val, "unit": unit, "error": None}
                    self.status_val[did] = format_status_value(result)
                except Exception as exc:
                    self.status_val[did] = f"ERR: {exc}"

        # Restore header to ECU's tx_id after status queries may have changed it
        if not self._quit:
            await self.terminal.set_header(self.tx_id)

    async def _status_poll_loop(self, interval: float = 3.0):
        """Background loop: poll all status params every `interval` seconds."""
        self._status_polling = True
        _tui_logger.info("Status poll loop started (interval=%.1fs)", interval)
        try:
            while not self._quit:
                if not self._busy:
                    try:
                        await self._poll_status_once()
                    except Exception as exc:
                        _tui_logger.warning("Status poll error: %s", exc)
                await asyncio.sleep(interval)
        finally:
            self._status_polling = False
            _tui_logger.info("Status poll loop ended")

    async def _release_all(self):
        """Send OFF for all active actuators."""
        active = [d for d in self.dids if self.state[d] == "on"]
        for did in active:
            try:
                await self._send_off(did)
            except Exception:
                pass

    async def _cleanup(self):
        """Release actuators and close session."""
        await self._release_all()
        if self._tester_task:
            self._tester_task.cancel()
            try:
                await self._tester_task
            except asyncio.CancelledError:
                pass

    def _draw(self):
        """Clear screen and draw the full frame."""
        # Move to top-left and clear screen
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write(self._render())
        sys.stdout.write("\n")
        sys.stdout.flush()

    async def run(self):
        """Main TUI event loop."""
        if not self.dids:
            print("  No IOControl DIDs available.")
            return

        # Set up debug log file (cleared each session)
        _LOGS_DIR.mkdir(exist_ok=True)
        handler = logging.FileHandler(_LOG_FILE, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        _tui_logger.addHandler(handler)
        _tui_logger.setLevel(logging.DEBUG)
        _tui_logger.info("TUI session start — %s (0x%03X), %d DIDs",
                         self.ecu_key, self.tx_id, len(self.dids))

        # Set up raw terminal for keypress reading
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        # Key input queue — filled by stdin reader
        key_queue: asyncio.Queue[str] = asyncio.Queue()

        def _on_stdin_ready():
            """Read keypress from raw stdin, enqueue it."""
            try:
                ch = os.read(fd, 16).decode("utf-8", errors="ignore")
                key_queue.put_nowait(ch)
            except Exception:
                pass

        loop = asyncio.get_event_loop()

        # Enter alternate screen buffer for clean rendering
        sys.stdout.write("\033[?1049h")  # enter alt screen
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()

        try:
            tty.setcbreak(fd)  # cbreak instead of raw — allows signal handling
            loop.add_reader(fd, _on_stdin_ready)

            # Start background status polling if any DIDs have status_param mapped
            has_status = any(self.cmds[d].get("status_param") for d in self.dids)
            poll_task = asyncio.ensure_future(self._status_poll_loop()) if has_status else None

            self._draw()

            while not self._quit:
                # Wait for keypress with timeout (allows periodic redraw)
                try:
                    key = await asyncio.wait_for(key_queue.get(), timeout=0.5)
                except (asyncio.TimeoutError, TimeoutError):
                    self._draw()
                    continue

                if key in ("q", "Q", "\x03"):  # q or Ctrl+C
                    if self._hex_input is not None:
                        # Cancel hex input mode
                        self._hex_input = None
                    else:
                        self._status = "Releasing actuators..."
                        self._draw()
                        self._quit = True
                elif self._hex_input is not None:
                    # Hex input mode — capture hex chars, backspace, enter, escape
                    if key == "\x1b":  # Escape — cancel input
                        self._hex_input = None
                    elif key == "\r":  # Enter — send value
                        hex_str = self._hex_input.strip()
                        self._hex_input = None
                        if hex_str and len(hex_str) % 2 == 0 and not self._busy:
                            did = self.dids[self.cursor]
                            value_bytes = bytes.fromhex(hex_str)
                            await self._send_adjust(did, value_bytes)
                        elif hex_str:
                            self._status = "Invalid: need even number of hex chars"
                    elif key in ("\x7f", "\x08"):  # Backspace/Delete
                        self._hex_input = self._hex_input[:-1]
                    elif len(key) == 1 and key.lower() in "0123456789abcdef":
                        self._hex_input += key.upper()
                    # Ignore other keys in hex input mode
                elif key in ("\x1b[A", "k"):  # Up arrow or k
                    self.cursor = max(0, self.cursor - 1)
                elif key in ("\x1b[B", "j"):  # Down arrow or j
                    self.cursor = min(len(self.dids) - 1, self.cursor + 1)
                elif key in ("\r", " "):  # Enter or Space — toggle ON/OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self._toggle(did)
                elif key in ("o", "O"):  # Explicit OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self._send_off(did)
                elif key in ("v", "V"):  # Enter hex value input mode
                    if not self._busy:
                        self._hex_input = ""
                elif key == "+" and not self._busy:  # Increment last value
                    did = self.dids[self.cursor]
                    if did in self.last_value:
                        val = int.from_bytes(self.last_value[did], "big") + 1
                        n = len(self.last_value[did])
                        if val < (1 << (8 * n)):
                            await self._send_adjust(did, val.to_bytes(n, "big"))
                elif key == "-" and not self._busy:  # Decrement last value
                    did = self.dids[self.cursor]
                    if did in self.last_value:
                        val = int.from_bytes(self.last_value[did], "big") - 1
                        n = len(self.last_value[did])
                        if val >= 0:
                            await self._send_adjust(did, val.to_bytes(n, "big"))

                self._draw()

        finally:
            loop.remove_reader(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            # Cancel background poll task
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
            # Capture final render before leaving alternate screen
            final_render = self._render()
            # Leave alternate screen buffer
            sys.stdout.write("\033[?25h")  # show cursor
            sys.stdout.write("\033[?1049l")  # leave alt screen
            sys.stdout.flush()
            # Print final state to main screen
            print(final_render)
            await self._cleanup()
            _tui_logger.info("TUI session end")
            print(f"  Debug log: {_LOG_FILE}")


async def mode_iocontrol_tui(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecu_name: str,
    verbose: bool = False,
):
    """Interactive IOControl TUI for an ECU."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        print(f"  No IOControl DIDs for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with IOControl: {', '.join(available)}")
        return

    tui = _IOControlTUI(terminal, ecu_key, ioctrl_index[ecu_key], pids_data=pids_data, verbose=verbose)
    await tui.run()
