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

    hdr = f"  {'DID':<{did_w}}  {'Label':<{label_w}}  {'ON cmd':<{on_w}}  {'OFF cmd':<{off_w}}  Verified  Hold"
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    for did, c in cmds.items():
        v = "✓" if c["verified"] else " "
        h = "✓" if c["hold"] else " "
        print(
            f"  {did:<{did_w}}  {c['label']:<{label_w}}  {c['on']:<{on_w}}  {c['off']:<{off_w}}  "
            f"   {v}        {h}"
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

    if not hex_cmd:
        print(f"  No {action} command defined for {ecu_key} {did_key} ({label})")
        return

    print(f"\n  {ecu_key} 0x{tx_id:03X} -- {label} -- {action}")
    print(f"  Command: {hex_cmd}")

    await terminal.set_header(tx_id)

    tester_task = None
    if needs_session:
        if verbose:
            print("  Entering extended diagnostic session (10 03)...")
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

    print()


# ── TUI ──────────────────────────────────────────────────────────────────────


class _IOControlTUI:
    """Interactive IOControl TUI — navigate DIDs and toggle ON/OFF.

    Keys:
        ↑/↓ or j/k  Navigate
        Enter/Space  Toggle ON (or OFF if already ON)
        o            Send explicit OFF
        q / Ctrl+C   Quit (auto-OFF any active actuator)
    """

    def __init__(
        self,
        terminal: WiCANTerminal,
        ecu_key: str,
        ecu_info: dict,
        verbose: bool = False,
    ):
        self.terminal = terminal
        self.ecu_key = ecu_key
        self.tx_id = ecu_info["tx_id"]
        self.cmds = ecu_info["cmds"]
        self.verbose = verbose

        # Ordered list of DID keys
        self.dids = list(self.cmds.keys())
        self.cursor = 0

        # Per-DID state: None=idle, "on"=active, "off"=sent off, "error"=failed
        self.state: dict[str, str | None] = {d: None for d in self.dids}
        self.last_response: dict[str, str] = {}  # DID → last response text

        self._session_active = False
        self._tester_task: asyncio.Task | None = None
        self._quit = False
        self._busy = False  # prevent concurrent CAN commands
        self._status = ""  # bottom status line

    def _render(self) -> str:
        """Build the display as a plain string with ANSI codes."""
        lines = []
        lines.append(f"\033[1;36m  IOControl TUI — {self.ecu_key} (0x{self.tx_id:03X})\033[0m")
        sess = "\033[32mactive\033[0m" if self._session_active else "\033[2mnot started\033[0m"
        lines.append(f"\033[2m  Session: \033[0m{sess}")
        lines.append("")

        # Column widths
        did_w = max(len(d) for d in self.dids) if self.dids else 4
        label_w = max(len(self.cmds[d]["label"]) for d in self.dids) if self.dids else 5

        # Header
        lines.append(
            f"\033[2m     {'DID':<{did_w}}  {'Label':<{label_w}}  State     Response\033[0m"
        )
        lines.append(f"\033[2m     {'─' * (did_w + label_w + 30)}\033[0m")

        for i, did in enumerate(self.dids):
            cmd = self.cmds[did]
            is_cursor = i == self.cursor
            state = self.state[did]
            resp = self.last_response.get(did, "")

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

            bold_on = "\033[1m" if is_cursor else ""
            bold_off = "\033[0m" if is_cursor else ""
            resp_part = f"  \033[2m{resp}\033[0m" if resp else ""

            lines.append(
                f"{prefix}{bold_on}{did:<{did_w}}  {cmd['label']:<{label_w}}{bold_off}  "
                f"{state_part}  {v_part}{resp_part}"
            )

        lines.append("")
        if self._status:
            lines.append(f"  {self._status}")
        lines.append("\033[2m  ↑↓/jk Navigate  Enter/Space Toggle  o OFF  q Quit\033[0m")
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

            self._draw()

            while not self._quit:
                # Wait for keypress with timeout (allows periodic redraw)
                try:
                    key = await asyncio.wait_for(key_queue.get(), timeout=0.5)
                except (asyncio.TimeoutError, TimeoutError):
                    self._draw()
                    continue

                if key in ("q", "Q", "\x03"):  # q or Ctrl+C
                    self._status = "Releasing actuators..."
                    self._draw()
                    self._quit = True
                elif key in ("\x1b[A", "k"):  # Up arrow or k
                    self.cursor = max(0, self.cursor - 1)
                elif key in ("\x1b[B", "j"):  # Down arrow or j
                    self.cursor = min(len(self.dids) - 1, self.cursor + 1)
                elif key in ("\r", " "):  # Enter or Space
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self._toggle(did)
                elif key in ("o", "O"):  # Explicit OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self._send_off(did)

                self._draw()

        finally:
            loop.remove_reader(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
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

    tui = _IOControlTUI(terminal, ecu_key, ioctrl_index[ecu_key], verbose=verbose)
    await tui.run()
