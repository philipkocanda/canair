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
from pathlib import Path

from ..pids import build_iocontrol_index, load_pids
from ..pids_edit import PidsEditError, promote_discovery, update_iocontrol_field
from ..terminal import WiCANTerminal
from ..tui import terminal_columns as _terminal_columns
from ..tui import terminal_lines as _terminal_lines
from ..uds_parse import nrc_abbrev
from ._iocontrol_render import _truncate_text, render_iocontrol
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
    term_w = _terminal_columns()
    did_w = max(len(d) for d in cmds) if cmds else 4
    label_w = max(len(c["label"]) for c in cmds.values()) if cmds else 5
    on_w = max(len(c["on"]) for c in cmds.values()) if cmds else 2
    off_w = max(len(c["off"]) for c in cmds.values()) if cmds else 3
    sp_w = max((len(c.get("status_param") or "") for c in cmds.values()), default=0)
    sp_w = max(sp_w, len("Read PID"))
    fixed_w = 2 + did_w + 2 + label_w + 2 + on_w + 2 + off_w + 2 + 2 + len("Verified  Hold")
    sp_w = min(sp_w, 100, max(len("Read PID"), term_w - fixed_w))

    hdr = (
        f"  {'DID':<{did_w}}  {'Label':<{label_w}}  {'ON cmd':<{on_w}}  "
        f"{'OFF cmd':<{off_w}}  {'Read PID':<{sp_w}}  Verified  Hold"
    )
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    for did, c in cmds.items():
        v = "✓" if c["verified"] else " "
        h = "✓" if c["hold"] else " "
        sp = c.get("status_param") or ""
        print(
            f"  {did:<{did_w}}  {c['label']:<{label_w}}  {c['on']:<{on_w}}  {c['off']:<{off_w}}  "
            f"{_truncate_text(sp, sp_w):<{sp_w}}     {v}        {h}"
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
        print(f"  Readback PID: {status_param}")

    await terminal.set_header(tx_id)

    tester_task = None
    if needs_session:
        if verbose:
            print("  Entering extended diagnostic session (10 03)...")
        _, tester_task = await terminal.enter_extended_session()

    # Query the mapped readback PID before executing. This is a best-effort hint
    # only: IOControl overrides may drive an output without changing the ECU
    # status bit or register that the readback PID exposes.
    if status_param:
        before = await query_param_status(terminal, pids_data, status_param, verbose=verbose)
        await terminal.set_header(tx_id)  # restore header after status query may have changed it
        if before["error"]:
            print(f"  Readback before: ERR {before['error']}")
        else:
            print(f"  Readback before: {status_param} = {format_status_value(before)}")

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

        # Query the mapped readback PID after command/release. Same caveat as
        # above: it may reflect normal ECU logic rather than the temporary
        # IOControl override.
        if status_param:
            await terminal.set_header(tx_id)
            after = await query_param_status(terminal, pids_data, status_param, verbose=verbose)
            if after["error"]:
                print(f"  Readback after: ERR {after['error']}")
            else:
                print(f"  Readback after: {status_param} = {format_status_value(after)}")

    print()


# ── TUI ──────────────────────────────────────────────────────────────────────


class _IOControlTUI:
    """Interactive IOControl TUI — navigate DIDs and toggle ON/OFF.

    Keys:
        ↑/↓ or j/k  Navigate
        Enter/Space  Toggle ON (or OFF if already ON). If the DID has no
                     simple ON command, opens the value prompt instead
                     (seeded with the last value sent this session, or 00).
        o            Send explicit OFF (ReturnControlToECU 00)
        v            Enter hex value → send ShortTermAdjustment (2F{DID}03{hex})
        +/-          Increment/decrement last value sent to current DID
        e            Edit label (writes to ecus/<ecu>.yaml)
        n            Edit notes (writes to ecus/<ecu>.yaml)
        m            Toggle verified (writes to ecus/<ecu>.yaml)
        d            Cycle view: curated / all / discoveries
        P            Promote current discovery to curated entry (on/off
                     inferred from response length; prompts for label)
        q / Ctrl+C   Quit (auto-OFF any active actuator)
    """

    def __init__(
        self,
        terminal: WiCANTerminal,
        ecu_key: str,
        ecu_info: dict,
        pids_data: dict,
        verbose: bool = False,
        poll: bool = False,
    ):
        self.terminal = terminal
        self.ecu_key = ecu_key
        self.tx_id = ecu_info["tx_id"]
        # Full cmd map (curated + discoveries); filtered view is in self.cmds.
        self.all_cmds = ecu_info["cmds"]
        self.pids_data = pids_data
        self.verbose = verbose
        # Background status polling is OPT-IN. When enabled, the poll loop sends
        # 2F{DID}00 (returnControlToECU) to every DID — which can actuate
        # relay/solenoid-backed DIDs (audible click). Off by default so merely
        # launching the TUI sends no CAN traffic until the user acts.
        self._poll_enabled = poll

        # View mode cycles through:
        #   "curated"     — only hand-authored iocontrol: entries
        #   "all"         — curated + scanner-discovered entries
        #   "discoveries" — only unpromoted discoveries (for triage)
        # Starts on "curated" so existing muscle memory is unchanged; user
        # presses 'd' to cycle to review newly scanned DIDs.
        self.view_mode = "curated"
        self._apply_view_filter()

        self.cursor = 0

        # Per-DID state: None=idle, "on"=active, "off"=sent off, "error"=failed
        self.state: dict[str, str | None] = dict.fromkeys(self.dids)
        self.last_response: dict[str, str] = {}  # DID → last response text
        self.last_value: dict[str, bytes] = {}  # DID → last ShortTermAdjustment value bytes

        # Per-DID status: trailing bytes (controlStatusRecord) from the last
        # 0x2F response for this DID, rendered as hex. Populated by both the
        # background poll loop (2F {DID} 00 returnControlToECU) and by every
        # ON/OFF/ADJUST send. None = never seen a response yet.
        self.status_bytes: dict[str, str | None] = dict.fromkeys(self.dids)
        self._status_polling = False  # True while background poll loop is running

        self._session_active = False
        self._tester_task: asyncio.Task | None = None
        self._quit = False
        self._busy = False  # prevent concurrent CAN commands
        self._status = ""  # bottom status line
        self._hex_input: str | None = None  # None = not in input mode, "" = editing
        # Metadata-edit input state. Tuple of (field, buffer) where field is
        # one of "label" / "notes"; None = not in edit mode. verified is
        # toggled directly (no input buffer).
        self._edit_input: tuple[str, str] | None = None

        # Scroll viewport: index of the first DID rendered on screen. Auto-
        # adjusted in ``_clamp_viewport()`` so the cursor is always visible.
        self._scroll_top = 0

    def _apply_view_filter(self):
        """Populate ``self.cmds`` / ``self.dids`` from ``self.all_cmds`` per view mode.

        Preserves insertion order from ``all_cmds`` (which in turn reflects
        YAML order: curated first, then discoveries). Called by ``__init__``
        and every time ``self.view_mode`` changes.
        """
        if self.view_mode == "curated":
            filtered = {d: c for d, c in self.all_cmds.items() if not c.get("discovery")}
        elif self.view_mode == "discoveries":
            filtered = {d: c for d, c in self.all_cmds.items() if c.get("discovery")}
        else:  # "all"
            filtered = dict(self.all_cmds)
        self.cmds = filtered
        self.dids = list(filtered.keys())

    def _cycle_view(self):
        """Advance ``view_mode`` and re-apply the filter. Resets cursor/scroll."""
        order = ("curated", "all", "discoveries")
        try:
            idx = order.index(self.view_mode)
        except ValueError:
            idx = 0
        # Skip modes that would produce an empty list (e.g. no discoveries
        # in this ECU at all) so the user doesn't hit a blank screen.
        for step in range(1, len(order) + 1):
            candidate = order[(idx + step) % len(order)]
            probe_cmds = self.all_cmds
            if candidate == "curated":
                probe = [d for d, c in probe_cmds.items() if not c.get("discovery")]
            elif candidate == "discoveries":
                probe = [d for d, c in probe_cmds.items() if c.get("discovery")]
            else:
                probe = list(probe_cmds.keys())
            if probe:
                self.view_mode = candidate
                break
        self._apply_view_filter()
        # Reset per-DID state dicts for any new DIDs (missing keys would KeyError).
        for d in self.dids:
            self.state.setdefault(d, None)
            self.status_bytes.setdefault(d, None)
        self.cursor = 0
        self._scroll_top = 0

    def _render(self) -> str:
        """Build the display as a plain string with ANSI codes.

        Delegated to :func:`canlib.modes._iocontrol_render.render_iocontrol`,
        which reads this controller's state (and clamps ``_scroll_top``).
        """
        return render_iocontrol(self)

    async def _ensure_session(self):
        """Open extended session + TesterPresent if not already active."""
        if self._session_active:
            return
        _tui_logger.info("Opening extended session (10 03) on 0x%03X", self.tx_id)
        await self.terminal.set_header(self.tx_id)
        ok, self._tester_task = await self.terminal.enter_extended_session()
        self._session_active = ok
        _tui_logger.info("Session established: %s", ok)

    def _extract_status_bytes(self, did: str, resp: dict) -> None:
        """Extract the controlStatusRecord tail from a 0x2F response and store it.

        A positive 0x2F response is `6F {DID_HI} {DID_LO} [tail bytes...]`.
        We store the tail bytes (anything after the 3-byte echo) as an
        uppercase hex string in ``self.status_bytes[did]``.

        - Positive with tail: ``"AA BB"`` (space-separated bytes)
        - Positive with no tail: ``""``
        - Negative (NRC): ``f"NRC {nrc:02X} {abbrev}"``
        - Transport error: ``"ERR"``
        """
        if resp.get("ok"):
            b = resp.get("bytes") or []
            tail = b[3:] if len(b) >= 3 else []
            self.status_bytes[did] = " ".join(f"{x:02X}" for x in tail)
        elif resp.get("nrc") is not None:
            self.status_bytes[did] = f"NRC {resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
        else:
            self.status_bytes[did] = "ERR"

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
            self._extract_status_bytes(did, resp)
            if resp["ok"]:
                self.state[did] = "on"
                self.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[did] = "error"
                self.last_response[did] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
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
            self._extract_status_bytes(did, resp)
            if resp["ok"]:
                self.state[did] = "off"
                self.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[did] = "error"
                self.last_response[did] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
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
        """Toggle: if ON → OFF, otherwise → ON.

        If the DID has no simple ON command (``on: ""`` in the YAML), open
        the hex value prompt instead of erroring. This is the common case
        for HVAC F0xx actuators and other DIDs that require
        ShortTermAdjustment value bytes. The prompt is seeded with the last
        value sent to this DID in the current session (``last_value``) or
        ``00`` if none has been sent yet.
        """
        if self.state[did] == "on":
            await self._send_off(did)
            return
        if not self.cmds[did]["on"]:
            # No simple ON — open the hex value prompt. Seed with the last
            # value sent this session (if any) so +/- stepping still works
            # naturally afterwards.
            seed = self.last_value[did].hex().upper() if did in self.last_value else "00"
            self._hex_input = seed
            return
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
            self._extract_status_bytes(did, resp)
            if resp["ok"]:
                self.state[did] = "on"
                self.last_response[did] = resp["hex"]
                self.last_value[did] = value_bytes
            elif resp.get("nrc") is not None:
                self.state[did] = "error"
                self.last_response[did] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
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
        """Poll every DID once by sending ``2F {DID} 00`` (returnControlToECU).

        ISO 14229-1 §10.4 sub-function 00 is *returnControlToECU* — it hands the
        addressed I/O back to the ECU's own control logic. This is often benign,
        but it is **NOT a guaranteed silent read**: an ECU may re-assert the
        actuator's default drive state when control is returned, which on
        relay/solenoid-backed DIDs (e.g. IGPM door lock/unlock ``BC10``/``BC11``,
        trunk ``BC09``, charge-cable lock ``BC3F``/``BC41``, defogger ``BC0C``)
        produces an audible click. Because of this the whole poll loop is
        opt-in (``--poll``); ECUs that support the DID return a positive response
        including the current ``controlStatusRecord`` tail bytes, which are stored
        in ``self.status_bytes[did]`` and rendered in the "Status" column.

        Bails early if the TUI is busy (mid-ON/OFF/ADJUST) or quitting; the
        scheduling loop in ``_status_poll_loop`` retries on the next tick.

        Most 0x2F DIDs require an extended diagnostic session, so we open
        one (same mechanism as the ON/OFF paths) before the first poll.
        """
        # Ensure extended session — 0x2F typically returns NRC 7F without it.
        if not self._session_active:
            if self._quit or self._busy:
                return
            await self._ensure_session()
            if not self._session_active:
                _tui_logger.warning("poll: could not establish extended session")
                return

        # Make sure we're talking to the right ECU header.
        await self.terminal.set_header(self.tx_id)

        for did in self.dids:
            if self._quit or self._busy:
                return
            did_hex = did.upper()
            req = f"2F{did_hex}00"
            try:
                resp = await self.terminal.send_uds(req, timeout=3.0)
            except Exception as exc:
                self.status_bytes[did] = "ERR"
                _tui_logger.warning("poll %s exception: %s", did, exc)
                continue
            _tui_logger.debug("poll %s req=%s resp=%s", did, req, resp)
            self._extract_status_bytes(did, resp)

    async def _status_poll_loop(self, interval: float = 3.0):
        """Background loop: poll every DID's status bytes every ``interval`` seconds."""
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

    def _apply_edit(self, did: str, field: str, value) -> None:
        """Persist an edit to ecus/<ecu>.yaml and refresh in-memory state.

        Errors are surfaced via ``self._status``; the original on-disk file
        is left intact when a write fails (surgical edits only mutate on
        successful regex match).
        """
        try:
            fpath = update_iocontrol_field(self.ecu_key, did, field, value)
        except PidsEditError as exc:
            self._status = f"\033[31mEdit failed: {exc}\033[0m"
            _tui_logger.error("edit %s %s=%r: %s", did, field, value, exc)
            return

        # Reload pids_data from disk and rebuild the iocontrol index so the
        # TUI reflects the change. We mutate the existing dict in place so
        # any references held elsewhere stay valid. Preserve discoveries so
        # the current view-mode filter keeps working.
        try:
            fresh = load_pids()
            self.pids_data.clear()
            self.pids_data.update(fresh)
            idx = build_iocontrol_index(self.pids_data, include_discoveries=True)
            self.all_cmds = idx[self.ecu_key]["cmds"]
            self._apply_view_filter()
            for d in self.dids:
                self.state.setdefault(d, None)
                self.status_bytes.setdefault(d, None)
        except Exception as exc:  # reload failure is non-fatal; in-memory stale
            _tui_logger.warning("reload after edit failed: %s", exc)

        self._status = f"\033[32mSaved {field} on {did} → {fpath.name}\033[0m"
        _tui_logger.info("edit %s %s=%r -> %s", did, field, value, fpath)

    def _apply_promote(self, did: str, label: str) -> None:
        """Promote a discovery DID to a curated entry and reload state.

        ``on``/``off`` are inferred from the discovery's captured response
        length (see ``pids_edit.promote_discovery``). The user refines the
        payload afterwards via ``e``/direct YAML edit if needed.
        """
        label = label.strip()
        if not label:
            self._status = "\033[31mPromote cancelled: empty label\033[0m"
            return
        try:
            fpath = promote_discovery(self.ecu_key, did, label)
        except PidsEditError as exc:
            self._status = f"\033[31mPromote failed: {exc}\033[0m"
            _tui_logger.error("promote %s label=%r: %s", did, label, exc)
            return

        # Reload from disk so the curated entry (with inferred on/off) is
        # picked up and the discovery disappears from the discoveries view.
        try:
            fresh = load_pids()
            self.pids_data.clear()
            self.pids_data.update(fresh)
            idx = build_iocontrol_index(self.pids_data, include_discoveries=True)
            self.all_cmds = idx[self.ecu_key]["cmds"]
            self._apply_view_filter()
            for d in self.dids:
                self.state.setdefault(d, None)
                self.status_bytes.setdefault(d, None)
            # Keep cursor on the same DID if still visible, else clamp.
            if did in self.dids:
                self.cursor = self.dids.index(did)
            else:
                self.cursor = min(self.cursor, max(0, len(self.dids) - 1))
        except Exception as exc:
            _tui_logger.warning("reload after promote failed: %s", exc)

        self._status = (
            f"\033[32mPromoted {did} → curated (on/off inferred) — verify & refine via 'e'\033[0m"
        )
        _tui_logger.info("promote %s label=%r -> %s", did, label, fpath)

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
        _tui_logger.info(
            "TUI session start — %s (0x%03X), %d DIDs", self.ecu_key, self.tx_id, len(self.dids)
        )

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

            # Start background status polling — ONLY when opted in via --poll.
            # The poll sends 2F {DID} 00 (returnControlToECU) to every DID. That
            # is NOT a guaranteed silent read: returnControlToECU hands the I/O
            # back to the ECU's own logic and can physically actuate relay/
            # solenoid-backed DIDs (e.g. IGPM door lock/unlock, trunk, charge
            # cable lock, defogger) — producing an audible click on the first
            # poll. So it is off by default; the user enables it explicitly.
            poll_task = (
                asyncio.ensure_future(self._status_poll_loop()) if self._poll_enabled else None
            )

            self._draw()

            while not self._quit:
                # Wait for keypress with timeout (allows periodic redraw)
                try:
                    key = await asyncio.wait_for(key_queue.get(), timeout=0.5)
                except TimeoutError:
                    self._draw()
                    continue

                if key == "\x03":  # Ctrl+C — always quits (even mid-edit)
                    if self._hex_input is not None:
                        self._hex_input = None
                    elif self._edit_input is not None:
                        self._edit_input = None
                    else:
                        self._status = "Releasing actuators..."
                        self._draw()
                        self._quit = True
                elif key in ("q", "Q") and self._hex_input is None and self._edit_input is None:
                    # q only quits in nav mode — allow it as a typed char in edits.
                    self._status = "Releasing actuators..."
                    self._draw()
                    self._quit = True
                elif self._edit_input is not None:
                    # Metadata-edit input mode — capture any printable text.
                    field, buf = self._edit_input
                    if key == "\x1b":  # Esc — cancel
                        self._edit_input = None
                    elif key in ("\r", "\n"):  # Enter — save
                        self._edit_input = None
                        did = self.dids[self.cursor]
                        if field == "promote":
                            self._apply_promote(did, buf)
                        else:
                            self._apply_edit(did, field, buf)
                    elif key in ("\x7f", "\x08"):  # Backspace
                        self._edit_input = (field, buf[:-1])
                    elif len(key) == 1 and (key.isprintable() or key == "\t"):
                        self._edit_input = (field, buf + key)
                    # Ignore other control sequences while editing metadata.
                elif self._hex_input is not None:
                    # Hex input mode — capture hex chars, backspace, enter, escape
                    if key == "\x1b":  # Escape — cancel input
                        self._hex_input = None
                    elif key in ("\r", "\n"):  # Enter — send value
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
                elif key in ("\x1b[5~", "\x02"):  # PageUp / Ctrl+B
                    page = max(1, _terminal_lines() - 10)
                    self.cursor = max(0, self.cursor - page)
                elif key in ("\x1b[6~", "\x06"):  # PageDown / Ctrl+F
                    page = max(1, _terminal_lines() - 10)
                    self.cursor = min(len(self.dids) - 1, self.cursor + page)
                elif key in ("\x1b[H", "g"):  # Home / g — jump to top
                    self.cursor = 0
                elif key in ("\x1b[F", "G"):  # End / G — jump to bottom
                    self.cursor = len(self.dids) - 1
                elif key in ("\r", "\n", " "):  # Enter or Space — toggle ON/OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self._toggle(did)
                elif key in ("o", "O"):  # Explicit OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self._send_off(did)
                elif key in ("v", "V"):  # Enter hex value input mode
                    if not self._busy:
                        did = self.dids[self.cursor]
                        # Seed with last value if available, otherwise blank
                        seed = self.last_value[did].hex().upper() if did in self.last_value else ""
                        self._hex_input = seed
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
                elif key == "e" and not self._busy:  # Edit label
                    did = self.dids[self.cursor]
                    self._edit_input = ("label", self.cmds[did].get("label", ""))
                elif key == "n" and not self._busy:  # Edit notes
                    did = self.dids[self.cursor]
                    current = (self.cmds[did].get("notes") or "").strip()
                    self._edit_input = ("notes", current)
                elif key == "m" and not self._busy:  # Toggle verified
                    did = self.dids[self.cursor]
                    new_val = not bool(self.cmds[did].get("verified", False))
                    self._apply_edit(did, "verified", new_val)
                elif key == "d" and not self._busy:  # Cycle view (curated/all/discoveries)
                    self._cycle_view()
                elif key == "P" and not self._busy:  # Promote discovery
                    did = self.dids[self.cursor]
                    if not self.cmds[did].get("discovery"):
                        self._status = (
                            "\033[33mNot a discovery — use 'e' to edit curated entries\033[0m"
                        )
                    else:
                        self._edit_input = ("promote", "")

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
    poll: bool = False,
):
    """Interactive IOControl TUI for an ECU."""
    ioctrl_index = build_iocontrol_index(pids_data, include_discoveries=True)
    ecu_key = ecu_name.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        print(f"  No IOControl DIDs for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with IOControl: {', '.join(available)}")
        return

    tui = _IOControlTUI(
        terminal, ecu_key, ioctrl_index[ecu_key], pids_data=pids_data, verbose=verbose, poll=poll
    )
    await tui.run()
