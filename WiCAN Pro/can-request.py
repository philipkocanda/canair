#!/usr/bin/env python3
"""Send custom CAN/UDS requests to the Ioniq via WiCAN's WebSocket terminal.

Uses the WiCAN ELM327 terminal mode over WebSocket (ws://<ip>/ws) to send
ELM327 AT commands and UDS requests. The firmware handles ISO-TP internally,
so multi-frame responses are reassembled automatically.

IMPORTANT: Using the WebSocket terminal overrides AutoPID mode. The WiCAN
must be rebooted after a terminal session for AutoPID (MQTT data feed to
Home Assistant) to resume. Use --reboot to reboot automatically on exit.

Modes:
    Interactive     python3 can-request.py
    Query params    python3 can-request.py --param SOC_BMS SOC_DISP
    Query ECU       python3 can-request.py --ecu BMS [--pid 2101]
    Raw request     python3 can-request.py --raw 7E4:2101
    Scan PIDs       python3 can-request.py --scan --tx 7E4 --service 21 --range 01-FF

Requires: websockets, pyyaml (requests optional, for --reboot)
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip3 install websockets", file=sys.stderr)
    sys.exit(1)

try:
    import requests as _requests_mod
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# Import expression evaluator from decode-captures.py
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from importlib import import_module

# Import evaluate_expression from decode-captures (hyphenated filename)
import importlib.util
_spec = importlib.util.spec_from_file_location("decode_captures", SCRIPT_DIR / "decode-captures.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
evaluate_expression = _mod.evaluate_expression

# ── Paths ──────────────────────────────────────────────────────────────────
PIDS_FILE = SCRIPT_DIR / "ioniq-2017-pids.yaml"

# ── WiCAN addresses ───────────────────────────────────────────────────────
WICAN_ADDRESSES = {
    "home": "10.0.2.86",
    "vpn": "192.168.3.2",
}
DEFAULT_WICAN = "home"

# ── ELM327 constants ─────────────────────────────────────────────────────
# UDS Negative Response Code descriptions
NRC_CODES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


# ── YAML Data Loading ────────────────────────────────────────────────────

def load_pids(path: Path = PIDS_FILE) -> dict:
    """Load PID definitions from YAML."""
    with open(path) as f:
        return yaml.safe_load(f)


def build_param_index(pids_data: dict) -> dict:
    """Build lookup: PARAM_NAME -> {ecu, tx_id, pid, expression, unit, ...}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        tx_id = ecu_def["tx_id"]
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            for param_name, param in pid_def.get("parameters", {}).items():
                index[param_name.upper()] = {
                    "ecu": ecu_name,
                    "tx_id": tx_id,
                    "pid": str(pid_code),
                    "expression": param.get("expression", ""),
                    "unit": param.get("unit", ""),
                    "verified": param.get("verified", False),
                    "ha_class": param.get("ha_class", ""),
                }
    return index


def build_ecu_index(pids_data: dict) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, pids: {PID: {parameters: ...}}}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "pids": {},
        }
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            index[ecu_name.upper()]["pids"][str(pid_code).upper()] = {
                "parameters": pid_def.get("parameters", {}),
                "period": pid_def.get("period", 5000),
                "enabled": pid_def.get("enabled", True),
            }
    return index


# ── ELM327 Response Parsing ──────────────────────────────────────────────

def parse_elm_response(raw: str) -> dict:
    """Parse an ELM327 response into structured data.

    Returns dict with keys:
        ok: bool - whether a positive response was received
        hex: str - raw hex string of response data (if ok)
        bytes: bytes - parsed response bytes (if ok)
        nrc: int - negative response code (if not ok)
        nrc_desc: str - NRC description (if not ok)
        error: str - error message (if parse failed)
        raw: str - original response text
    """
    result = {"raw": raw, "ok": False}

    # Strip whitespace, prompts, and echo
    lines = raw.strip().split("\n")
    lines = [l.strip() for l in lines if l.strip()]

    # Filter out echo lines (AT commands, request echo) and empty/prompt lines
    data_lines = []
    for line in lines:
        line = line.rstrip(">").strip()
        if not line:
            continue
        if line.startswith("AT") or line.startswith("at"):
            continue
        if line == "OK":
            continue
        if line == "?":
            result["error"] = "Unknown command"
            return result
        if line == "NO DATA":
            result["error"] = "No response from ECU (NO DATA)"
            return result
        if line == "CAN ERROR":
            result["error"] = "CAN bus error"
            return result
        if line == "UNABLE TO CONNECT":
            result["error"] = "Unable to connect to CAN bus"
            return result
        if line == "BUS INIT: ...ERROR":
            result["error"] = "Bus initialization error"
            return result
        if line == "STOPPED":
            result["error"] = "Request stopped"
            return result
        if line == "BUFFER FULL":
            result["error"] = "Response buffer full"
            return result
        data_lines.append(line)

    if not data_lines:
        result["error"] = "Empty response"
        return result

    # Filter out echo of the request (e.g., "2101" echoed back)
    # The echo is typically the first line and matches a short hex-only pattern
    # that's also the UDS request we sent. We detect it by checking if the first
    # line is much shorter than subsequent lines.
    if len(data_lines) > 1:
        first = data_lines[0].replace(" ", "")
        # If first line is <= 6 hex chars and looks like a request echo, skip it
        if len(first) <= 6 and all(c in "0123456789ABCDEFabcdef" for c in first):
            data_lines = data_lines[1:]

    # Check for multi-frame ISO-TP format: N:hexdata (e.g., "0:6101FFFFFFFF")
    # The ELM327 with ATAL (allow long messages) returns multi-frame responses
    # with a length header line followed by numbered frames.
    is_multiframe = any(re.match(r"^\d+:", line) for line in data_lines)

    if is_multiframe:
        # Filter out the length header (e.g., "03D" = 61 bytes)
        frame_lines = []
        for line in data_lines:
            m = re.match(r"^(\d+):([0-9A-Fa-f]+)$", line)
            if m:
                frame_lines.append((int(m.group(1)), m.group(2)))
            # else: skip length header and other non-frame lines

        # Sort by frame number and concatenate hex data
        frame_lines.sort(key=lambda x: x[0])
        hex_clean = "".join(hex_data for _, hex_data in frame_lines)
    else:
        # Single-frame: join all lines and strip spaces
        hex_str = " ".join(data_lines)
        hex_clean = hex_str.replace(" ", "")

    # Validate hex
    if not all(c in "0123456789ABCDEFabcdef" for c in hex_clean):
        result["error"] = f"Non-hex response: {hex_clean[:80]}"
        return result

    if len(hex_clean) < 2:
        result["error"] = f"Response too short: {hex_clean}"
        return result

    try:
        response_bytes = bytes.fromhex(hex_clean)
    except ValueError as e:
        result["error"] = f"Hex decode failed: {e}"
        return result

    result["hex"] = hex_clean.upper()
    result["bytes"] = response_bytes

    # Check for UDS negative response (7F <service> <NRC>)
    if response_bytes[0] == 0x7F and len(response_bytes) >= 3:
        nrc = response_bytes[2]
        result["nrc"] = nrc
        result["nrc_service"] = response_bytes[1]
        result["nrc_desc"] = NRC_CODES.get(nrc, f"unknown (0x{nrc:02X})")
        return result

    result["ok"] = True
    return result


def elm_hex_to_wican_bytes(hex_str: str) -> bytes:
    """Convert ELM327 reassembled payload to WiCAN AutoPID byte layout.

    WiCAN AutoPID runs with ELM327 headers ON and spaces ON. Its
    parse_elm327_response() copies ALL 8 CAN data bytes from each frame
    (including PCI bytes) sequentially into response.data. This means:

      Frame 0 (First Frame):  [10 LL] [SID SUB d d d d]  → 8 bytes
      Frame 1 (Consecutive):  [21]    [d d d d d d d]    → 8 bytes
      Frame 2 (Consecutive):  [22]    [d d d d d d d]    → 8 bytes
      ...

    Byte indices in expressions (B09, B37, etc.) reference this interleaved
    format. B0=PCI, B1=length_lo, B2=SID, B8=PCI_CF1, B9=first_data_byte_CF1.

    The ELM327 terminal (our transport) returns ONLY the reassembled UDS
    payload without PCI. We must reconstruct the AutoPID layout by
    re-inserting the PCI bytes at the correct positions.

    For single-frame responses (<=7 UDS bytes): PCI is 1 byte (0x0N).
    For multi-frame responses (>6 UDS bytes):
      - First frame PCI: 2 bytes (0x10 | (len>>8), len & 0xFF)
      - Consecutive frame PCI: 1 byte each (0x20 | (seq & 0x0F))
    """
    data = bytes.fromhex(hex_str)
    payload_len = len(data)

    if payload_len <= 7:
        # Single frame: PCI = 0x0N (N = payload length)
        return bytes([payload_len]) + data
    else:
        # Multi-frame: reconstruct CAN frame layout
        result = bytearray()

        # First frame: 2 PCI bytes + 6 data bytes
        pci_hi = 0x10 | ((payload_len >> 8) & 0x0F)
        pci_lo = payload_len & 0xFF
        result.extend([pci_hi, pci_lo])
        result.extend(data[:6])

        # Consecutive frames: 1 PCI byte + 7 data bytes each
        offset = 6
        seq = 1
        while offset < payload_len:
            pci_cf = 0x20 | (seq & 0x0F)
            result.append(pci_cf)
            chunk = data[offset:offset + 7]
            result.extend(chunk)
            # Pad last frame to 7 bytes if needed (CAN frames are always 8 bytes)
            if len(chunk) < 7:
                result.extend(b'\x00' * (7 - len(chunk)))
            offset += 7
            seq += 1

        return bytes(result)


def reboot_wican(host: str):
    """Reboot WiCAN device via HTTP POST to restore AutoPID mode.

    After using the WebSocket terminal, the device stays in terminal mode
    and AutoPID (which feeds MQTT/HA) does not resume until rebooted.
    """
    if not HAS_REQUESTS:
        print("  Cannot reboot: 'requests' module not installed. Run: pip3 install requests", file=sys.stderr)
        print(f"  Manual reboot: curl -X POST http://{host}/system_reboot -d reboot", file=sys.stderr)
        return False

    url = f"http://{host}/system_reboot"
    try:
        resp = _requests_mod.post(url, data="reboot", timeout=5)
        print(f"Rebooting WiCAN... ({resp.status_code})")
        return True
    except _requests_mod.RequestException as e:
        print(f"  FAILED to reboot: {e}", file=sys.stderr)
        return False


# ── WiCAN WebSocket Connection ───────────────────────────────────────────

class WiCANTerminal:
    """WebSocket connection to WiCAN in ELM327 terminal mode."""

    def __init__(self, host: str, timeout: float = 3.0, verbose: bool = False):
        self.host = host
        self.url = f"ws://{host}/ws"
        self.timeout = timeout
        self.verbose = verbose
        self.ws = None
        self._buffer = ""

    async def connect(self):
        """Connect to WiCAN and enter ELM327 terminal mode."""
        if self.verbose:
            print(f"  [ws] Connecting to {self.url}...", file=sys.stderr)

        self.ws = await websockets.connect(self.url, ping_interval=None)

        # Enter ELM327 terminal mode
        mode_msg = json.dumps({"ws_mode": "terminal", "terminal_type": "elm327"})
        await self.ws.send(mode_msg)
        if self.verbose:
            print(f"  [ws] Sent: {mode_msg}", file=sys.stderr)

        # Wait for mode acknowledgment
        await asyncio.sleep(0.3)
        # Drain any initial messages
        await self._drain()

    async def close(self):
        """Close the WebSocket connection."""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def _drain(self):
        """Read and discard any pending messages."""
        while True:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=0.2)
                if self.verbose:
                    print(f"  [ws] Drained: {msg!r}", file=sys.stderr)
            except (asyncio.TimeoutError, Exception):
                break

    async def send_command(self, cmd: str, timeout: float = None) -> str:
        """Send an ELM327 command and wait for the response.

        Args:
            cmd: ELM327 command (without CR terminator)
            timeout: Response timeout in seconds (default: self.timeout)

        Returns:
            Raw response text (may contain multiple lines)
        """
        if timeout is None:
            timeout = self.timeout

        # Send command with CR terminator
        await self.ws.send(cmd + "\r")
        if self.verbose:
            print(f"  [ws] Sent: {cmd!r}", file=sys.stderr)

        # Collect response until we see the > prompt or timeout
        response_parts = []
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=min(remaining, 0.5))
            except asyncio.TimeoutError:
                # Check if we have a complete response
                if response_parts:
                    text = "".join(response_parts)
                    if ">" in text or "\r" in text:
                        break
                continue
            except websockets.exceptions.ConnectionClosed:
                raise ConnectionError("WebSocket connection closed")

            # Parse message — could be JSON or plain text
            if isinstance(msg, str):
                try:
                    parsed = json.loads(msg)
                    if parsed.get("type") == "term_out":
                        data = parsed["data"]
                        response_parts.append(data)
                        if self.verbose:
                            print(f"  [ws] Recv (term_out): {data!r}", file=sys.stderr)
                    elif parsed.get("type") == "ws_mode":
                        if self.verbose:
                            print(f"  [ws] Mode ack: {parsed}", file=sys.stderr)
                        continue
                    else:
                        if self.verbose:
                            print(f"  [ws] Recv (json): {parsed}", file=sys.stderr)
                except json.JSONDecodeError:
                    response_parts.append(msg)
                    if self.verbose:
                        print(f"  [ws] Recv (text): {msg!r}", file=sys.stderr)

            # Check for prompt indicating end of response
            full = "".join(response_parts)
            if ">" in full:
                break

        raw = "".join(response_parts)
        # Strip the prompt character and normalize line endings
        raw = raw.replace(">", "").replace("\r\n", "\n").replace("\r", "\n")
        # Strip control characters except newline
        raw = re.sub(r"[\x00-\x09\x0b-\x1f]", "", raw)
        return raw.strip()

    async def init_elm(self, init_string: str = "ATSP6;ATS0;ATAL;ATST96;"):
        """Send ELM327 initialization commands.

        Args:
            init_string: Semicolon-separated AT commands from YAML init field
        """
        # Always reset first
        resp = await self.send_command("ATZ", timeout=3.0)
        if self.verbose:
            print(f"  [init] ATZ -> {resp!r}", file=sys.stderr)

        # Send init commands
        for cmd in init_string.rstrip(";").split(";"):
            cmd = cmd.strip()
            if not cmd:
                continue
            resp = await self.send_command(cmd)
            if self.verbose:
                print(f"  [init] {cmd} -> {resp!r}", file=sys.stderr)

    async def set_header(self, tx_id: int):
        """Set the ELM327 header (target ECU TX ID).

        Sends both ATSH and ATFCSH to set header and flow control header.
        """
        hex_id = f"{tx_id:03X}"
        resp = await self.send_command(f"ATSH{hex_id}")
        if self.verbose:
            print(f"  [header] ATSH{hex_id} -> {resp!r}", file=sys.stderr)

        resp = await self.send_command(f"ATFCSH{hex_id}")
        if self.verbose:
            print(f"  [header] ATFCSH{hex_id} -> {resp!r}", file=sys.stderr)

    async def send_uds(self, service_pid: str, timeout: float = None) -> dict:
        """Send a UDS request and parse the response.

        Args:
            service_pid: UDS request hex string (e.g., "2101", "22C00B")

        Returns:
            Parsed response dict from parse_elm_response()
        """
        raw = await self.send_command(service_pid, timeout=timeout)
        return parse_elm_response(raw)


# ── Output Formatting ────────────────────────────────────────────────────

def format_value(value: float, unit: str) -> str:
    """Format a decoded value with unit."""
    if value == int(value):
        return f"{int(value)} {unit}".strip()
    return f"{value:.2f} {unit}".strip()


def print_decoded_params(params_results: list, verbose: bool = False):
    """Print decoded parameter values in a table.

    Args:
        params_results: list of (name, value, unit, expression, error, verified)
    """
    if not params_results:
        print("  No parameters to display")
        return

    max_name = max(len(r[0]) for r in params_results)
    max_val = max(len(format_value(r[1], r[2]) if r[1] is not None else "ERROR") for r in params_results)

    for name, value, unit, expression, error, verified in params_results:
        v_mark = " " if verified else "?"
        if error:
            print(f"  {v_mark} {name:<{max_name}}  {'ERROR':<{max_val}}  !! {error}")
        else:
            val_str = format_value(value, unit)
            if verbose:
                print(f"  {v_mark} {name:<{max_name}}  {val_str:<{max_val}}  [{expression}]")
            else:
                print(f"  {v_mark} {name:<{max_name}}  {val_str}")


def print_hexdump(data: bytes, prefix: str = "  "):
    """Print a hex dump of raw bytes."""
    for row_start in range(0, len(data), 16):
        row_end = min(row_start + 16, len(data))
        hex_part = " ".join(f"{data[j]:02X}" for j in range(row_start, row_end))
        idx_part = " ".join(f"{j:2d}" for j in range(row_start, row_end))
        print(f"{prefix}Idx:  {idx_part}")
        print(f"{prefix}Hex:  {hex_part}")
        print()


def print_json_result(result: dict):
    """Print result as JSON for machine consumption."""
    # Convert bytes to hex for JSON serialization
    out = {}
    for k, v in result.items():
        if isinstance(v, bytes):
            out[k] = v.hex().upper()
        else:
            out[k] = v
    print(json.dumps(out, indent=2))


# ── Mode Implementations ─────────────────────────────────────────────────

async def mode_interactive(terminal: WiCANTerminal, pids_data: dict, verbose: bool):
    """Interactive REPL mode — type ELM327/UDS commands directly."""
    print("WiCAN ELM327 Terminal — Interactive Mode")
    print(f"Connected to {terminal.host}")
    print()
    print("Commands:")
    print("  AT commands    ATZ, ATSH7E4, ATS0, etc.")
    print("  UDS requests   2101, 22C00B, etc. (set header first with ATSH)")
    print("  !decode        Decode last response using YAML definitions")
    print("  !hexdump       Show hex dump of last response")
    print("  !info <ECU>    Show ECU info from YAML (e.g., !info BMS)")
    print("  !list          List all known ECUs")
    print("  !reboot        Reboot WiCAN to restore AutoPID mode")
    print("  !quit / Ctrl+C Exit")
    print()

    ecu_index = build_ecu_index(pids_data)
    param_index = build_param_index(pids_data)
    last_response = None
    last_tx_id = None

    while True:
        try:
            cmd = await asyncio.get_event_loop().run_in_executor(None, lambda: input("> "))
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        cmd = cmd.strip()
        if not cmd:
            continue

        # Built-in commands
        if cmd.lower() in ("!quit", "!exit", "!q"):
            print("Bye!")
            break

        if cmd.lower() == "!reboot":
            reboot_wican(terminal.host)
            break

        if cmd.lower() == "!list":
            print(f"\n{'ECU':<10} {'TX ID':<8} {'PIDs'}")
            print("─" * 40)
            for name, info in sorted(ecu_index.items()):
                pids = ", ".join(sorted(info["pids"].keys()))
                print(f"{name:<10} 0x{info['tx_id']:03X}    {pids}")
            print()
            continue

        if cmd.lower().startswith("!info "):
            ecu_name = cmd[6:].strip().upper()
            if ecu_name not in ecu_index:
                print(f"  Unknown ECU: {ecu_name}. Use !list to see available ECUs.")
                continue
            info = ecu_index[ecu_name]
            print(f"\n  {ecu_name} — TX 0x{info['tx_id']:03X}")
            for pid_code, pid_info in sorted(info["pids"].items()):
                n_params = len(pid_info["parameters"])
                enabled = "enabled" if pid_info["enabled"] else "disabled"
                print(f"    PID {pid_code} ({enabled}, {pid_info['period']}ms, {n_params} params)")
                for pname in sorted(pid_info["parameters"].keys()):
                    pdef = pid_info["parameters"][pname]
                    v = "+" if pdef.get("verified") else "?"
                    print(f"      {v} {pname}: {pdef.get('expression', '')} [{pdef.get('unit', '')}]")
            print()
            continue

        if cmd.lower() == "!decode":
            if last_response is None or not last_response.get("ok"):
                print("  No successful response to decode. Send a UDS request first.")
                continue
            # Try to find matching parameters for the response
            if last_tx_id is None:
                print("  No TX ID set. Use ATSH to set the ECU header first.")
                continue
            # Find the UDS service+PID from the response
            resp_bytes = last_response["bytes"]
            if resp_bytes[0] == 0x61:
                pid_str = f"21{resp_bytes[1]:02X}"
            elif resp_bytes[0] == 0x62:
                pid_str = f"22{resp_bytes[1]:02X}{resp_bytes[2]:02X}"
            else:
                print(f"  Unknown response SID: 0x{resp_bytes[0]:02X}")
                continue

            # Find matching ECU/PID in YAML
            found = False
            for ecu_name, ecu_info in ecu_index.items():
                if ecu_info["tx_id"] != last_tx_id:
                    continue
                pid_upper = pid_str.upper()
                if pid_upper in ecu_info["pids"]:
                    wican_bytes = elm_hex_to_wican_bytes(last_response["hex"])
                    params = ecu_info["pids"][pid_upper]["parameters"]
                    results = []
                    for pname, pdef in params.items():
                        expr = pdef.get("expression", "")
                        unit = pdef.get("unit", "")
                        verified = pdef.get("verified", False)
                        if not expr:
                            continue
                        try:
                            value = evaluate_expression(expr, wican_bytes)
                            value = round(value * 100) / 100
                            results.append((pname, value, unit, expr, None, verified))
                        except Exception as e:
                            results.append((pname, None, unit, expr, str(e), verified))
                    print(f"\n  {ecu_name} — PID {pid_str} — TX 0x{last_tx_id:03X}")
                    print_decoded_params(results, verbose=verbose)
                    print()
                    found = True
                    break
            if not found:
                print(f"  No YAML definition for TX 0x{last_tx_id:03X} PID {pid_str}")
            continue

        if cmd.lower() == "!hexdump":
            if last_response is None:
                print("  No response to dump.")
                continue
            if "bytes" in last_response:
                print(f"\n  Raw ELM response ({len(last_response['bytes'])} bytes):")
                print_hexdump(last_response["bytes"])
                wican_bytes = elm_hex_to_wican_bytes(last_response["hex"])
                print(f"  WiCAN-indexed ({len(wican_bytes)} bytes, with PCI prefix):")
                print_hexdump(wican_bytes)
            else:
                print(f"  Raw: {last_response.get('raw', '(none)')}")
            continue

        # Track ATSH commands to know current TX ID
        atsh_match = re.match(r"^ATSH\s*([0-9A-Fa-f]{3})$", cmd, re.IGNORECASE)
        if atsh_match:
            last_tx_id = int(atsh_match.group(1), 16)

        # Send command to WiCAN
        try:
            raw = await terminal.send_command(cmd)
            print(raw)

            # If it looks like a UDS response (hex data), parse it
            response = parse_elm_response(raw)
            if response.get("ok") or response.get("nrc") is not None:
                last_response = response

                if response.get("nrc") is not None:
                    nrc = response["nrc"]
                    svc = response.get("nrc_service", 0)
                    desc = response.get("nrc_desc", "unknown")
                    print(f"  [NRC] Service 0x{svc:02X} rejected: 0x{nrc:02X} ({desc})")

        except Exception as e:
            print(f"  Error: {e}")
    print()


async def mode_param(terminal: WiCANTerminal, pids_data: dict, param_names: list[str],
                     verbose: bool, as_json: bool):
    """Query specific named parameters."""
    param_index = build_param_index(pids_data)

    # Group parameters by (tx_id, pid) to minimize requests
    groups: dict[tuple[int, str], list[dict]] = {}
    for name in param_names:
        key = name.upper()
        if key not in param_index:
            print(f"  Unknown parameter: {name}", file=sys.stderr)
            # Suggest close matches
            matches = [k for k in param_index if key in k]
            if matches:
                print(f"  Did you mean: {', '.join(matches[:5])}", file=sys.stderr)
            continue
        info = param_index[key]
        group_key = (info["tx_id"], info["pid"])
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append({**info, "name": key})

    if not groups:
        return

    all_results = []

    for (tx_id, pid), params in groups.items():
        ecu_name = params[0]["ecu"]

        # Set ECU header
        await terminal.set_header(tx_id)

        # Send UDS request
        response = await terminal.send_uds(pid)

        if not response["ok"]:
            error = response.get("error") or response.get("nrc_desc", "unknown error")
            if response.get("nrc") is not None:
                error = f"NRC 0x{response['nrc']:02X}: {response['nrc_desc']}"
            for p in params:
                all_results.append((p["name"], None, p["unit"], p["expression"], error, p["verified"]))
            continue

        # Convert to WiCAN byte indexing and decode
        wican_bytes = elm_hex_to_wican_bytes(response["hex"])

        for p in params:
            try:
                value = evaluate_expression(p["expression"], wican_bytes)
                value = round(value * 100) / 100
                all_results.append((p["name"], value, p["unit"], p["expression"], None, p["verified"]))
            except Exception as e:
                all_results.append((p["name"], None, p["unit"], p["expression"], str(e), p["verified"]))

    if as_json:
        json_out = []
        for name, value, unit, expr, error, verified in all_results:
            entry = {"name": name, "value": value, "unit": unit}
            if error:
                entry["error"] = error
            json_out.append(entry)
        print(json.dumps(json_out, indent=2))
    else:
        print()
        print_decoded_params(all_results, verbose=verbose)
        print()


async def mode_ecu(terminal: WiCANTerminal, pids_data: dict, ecu_name: str,
                   pid_filter: str | None, verbose: bool, as_json: bool):
    """Query all parameters for an ECU, optionally filtered by PID."""
    ecu_index = build_ecu_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in ecu_index:
        print(f"  Unknown ECU: {ecu_name}", file=sys.stderr)
        print(f"  Available: {', '.join(sorted(ecu_index.keys()))}", file=sys.stderr)
        return

    ecu_info = ecu_index[ecu_key]
    tx_id = ecu_info["tx_id"]

    # Set ECU header once
    await terminal.set_header(tx_id)

    print(f"\n  {ecu_key} — TX 0x{tx_id:03X}")

    all_json = []

    for pid_code, pid_info in sorted(ecu_info["pids"].items()):
        if pid_filter and pid_code.upper() != pid_filter.upper():
            continue

        parameters = pid_info["parameters"]
        if not parameters:
            continue

        print(f"\n  PID {pid_code}:")

        # Send UDS request
        response = await terminal.send_uds(pid_code)

        if not response["ok"]:
            error = response.get("error") or response.get("nrc_desc", "unknown error")
            if response.get("nrc") is not None:
                error = f"NRC 0x{response['nrc']:02X}: {response['nrc_desc']}"
            print(f"    Error: {error}")
            continue

        # Convert and decode
        wican_bytes = elm_hex_to_wican_bytes(response["hex"])

        if verbose:
            print(f"    Response: {response['hex']}")
            print(f"    WiCAN bytes ({len(wican_bytes)}): {wican_bytes.hex().upper()}")

        results = []
        for pname, pdef in parameters.items():
            expr = pdef.get("expression", "")
            unit = pdef.get("unit", "")
            verified = pdef.get("verified", False)
            if not expr:
                continue
            try:
                value = evaluate_expression(expr, wican_bytes)
                value = round(value * 100) / 100
                results.append((pname, value, unit, expr, None, verified))
            except Exception as e:
                results.append((pname, None, unit, expr, str(e), verified))

        if as_json:
            for name, value, unit, expr, error, verified in results:
                entry = {"ecu": ecu_key, "pid": pid_code, "name": name, "value": value, "unit": unit}
                if error:
                    entry["error"] = error
                all_json.append(entry)
        else:
            print_decoded_params(results, verbose=verbose)

    if as_json:
        print(json.dumps(all_json, indent=2))

    print()


async def mode_raw(terminal: WiCANTerminal, raw_spec: str, verbose: bool, as_json: bool):
    """Send a raw UDS request specified as TX_ID:SERVICE_PID."""
    # Parse spec: "7E4:2101" or "7E4 2101"
    match = re.match(r"^([0-9A-Fa-f]{3})[:\s]([0-9A-Fa-f]+)$", raw_spec)
    if not match:
        print(f"  Invalid format: {raw_spec}", file=sys.stderr)
        print(f"  Expected: <TX_ID>:<SERVICE_PID>  (e.g., 7E4:2101)", file=sys.stderr)
        return

    tx_id = int(match.group(1), 16)
    service_pid = match.group(2).upper()

    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}")

    await terminal.set_header(tx_id)
    response = await terminal.send_uds(service_pid)

    if as_json:
        print_json_result(response)
        return

    if not response["ok"]:
        error = response.get("error") or response.get("nrc_desc", "unknown error")
        if response.get("nrc") is not None:
            print(f"  NRC: 0x{response['nrc']:02X} — {response['nrc_desc']}")
            print(f"  Service: 0x{response.get('nrc_service', 0):02X}")
        else:
            print(f"  Error: {error}")
        return

    print(f"  Response ({len(response['bytes'])} bytes): {response['hex']}")
    print()
    print_hexdump(response["bytes"])


async def mode_scan(terminal: WiCANTerminal, tx_id: int, service: int,
                    pid_range: tuple[int, int], verbose: bool, as_json: bool):
    """Scan a range of PIDs and show which respond positively."""
    start, end = pid_range
    total = end - start + 1

    print(f"\n  Scanning TX 0x{tx_id:03X}, service 0x{service:02X}, PIDs 0x{start:02X}..0x{end:02X} ({total} PIDs)")
    print()

    await terminal.set_header(tx_id)

    positive = []
    negative = []
    errors = []

    for pid_val in range(start, end + 1):
        # Build the request string
        if service == 0x22:
            # Service 0x22 uses 2-byte DIDs
            req = f"{service:02X}{pid_val:04X}"
        else:
            req = f"{service:02X}{pid_val:02X}"

        response = await terminal.send_uds(req, timeout=2.0)

        status = ""
        if response["ok"]:
            n_bytes = len(response["bytes"])
            positive.append((pid_val, response))
            status = f"  + 0x{pid_val:02X}: OK ({n_bytes} bytes)"
            if verbose:
                status += f"  {response['hex']}"
            print(status)
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            negative.append((pid_val, nrc, desc))
            if verbose:
                print(f"  - 0x{pid_val:02X}: NRC 0x{nrc:02X} ({desc})")
        else:
            error = response.get("error", "unknown")
            errors.append((pid_val, error))
            if verbose:
                print(f"  ! 0x{pid_val:02X}: {error}")

        # Progress indicator (non-verbose)
        if not verbose and not response["ok"]:
            # Print progress every 16 PIDs
            if (pid_val - start + 1) % 16 == 0:
                pct = (pid_val - start + 1) / total * 100
                print(f"  ... {pid_val - start + 1}/{total} ({pct:.0f}%)", end="\r", file=sys.stderr)

    print(f"\n  ─── Scan Results ────────────────────────")
    print(f"  Positive: {len(positive)}")
    print(f"  Negative: {len(negative)}")
    print(f"  Errors:   {len(errors)}")

    if positive:
        print(f"\n  Responding PIDs:")
        for pid_val, resp in positive:
            n = len(resp["bytes"])
            print(f"    0x{pid_val:02X} — {n} bytes: {resp['hex'][:60]}{'...' if len(resp['hex']) > 60 else ''}")

    if as_json:
        out = {
            "tx_id": f"0x{tx_id:03X}",
            "service": f"0x{service:02X}",
            "range": f"0x{start:02X}-0x{end:02X}",
            "positive": [{"pid": f"0x{p:02X}", "bytes": r["hex"]} for p, r in positive],
            "negative": [{"pid": f"0x{p:02X}", "nrc": f"0x{n:02X}", "desc": d} for p, n, d in negative],
            "errors": [{"pid": f"0x{p:02X}", "error": e} for p, e in errors],
        }
        print(json.dumps(out, indent=2))

    print()


# ── Main ──────────────────────────────────────────────────────────────────

def parse_range(range_str: str) -> tuple[int, int]:
    """Parse a PID range like '01-FF' or '00-1F'."""
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        raise argparse.ArgumentTypeError(f"Invalid range: {range_str}. Expected format: 01-FF")
    return int(match.group(1), 16), int(match.group(2), 16)


async def async_main(args):
    """Main async entry point."""
    # Resolve WiCAN address
    host = args.wican
    if host in WICAN_ADDRESSES:
        host = WICAN_ADDRESSES[host]

    # Load YAML definitions
    pids_data = load_pids()
    init_string = pids_data.get("init", "ATSP6;ATS0;ATAL;ATST96;")

    # Create terminal connection
    terminal = WiCANTerminal(
        host=host,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    try:
        print(f"Connecting to WiCAN at {host}...")
        await terminal.connect()
        print("Connected. Initializing ELM327...")
        await terminal.init_elm(init_string)
        print("Ready.")

        # Dispatch to mode
        if args.param:
            await mode_param(terminal, pids_data, args.param, args.verbose, args.json)
        elif args.ecu:
            await mode_ecu(terminal, pids_data, args.ecu, args.pid, args.verbose, args.json)
        elif args.raw:
            await mode_raw(terminal, args.raw, args.verbose, args.json)
        elif args.scan:
            if not args.tx:
                print("Error: --scan requires --tx (ECU TX ID)", file=sys.stderr)
                sys.exit(1)
            tx_id = int(args.tx, 16)
            service = int(args.service, 16) if args.service else 0x21
            pid_range = parse_range(args.range) if args.range else (0x01, 0xFF)
            await mode_scan(terminal, tx_id, service, pid_range, args.verbose, args.json)
        else:
            # Interactive mode
            await mode_interactive(terminal, pids_data, args.verbose)

    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except websockets.exceptions.InvalidURI as e:
        print(f"Invalid WebSocket URI: {e}", file=sys.stderr)
        sys.exit(1)
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"WebSocket closed: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await terminal.close()

        # Reboot to restore AutoPID mode, or warn
        if args.reboot:
            reboot_wican(host)
        else:
            print("NOTE: WiCAN is now in terminal mode. AutoPID (MQTT/HA data feed) is paused.")
            print("      Run with --reboot or reboot manually to restore AutoPID.")


def main():
    parser = argparse.ArgumentParser(
        description="Send custom CAN/UDS requests via WiCAN WebSocket terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                  Interactive REPL
  %(prog)s --param SOC_BMS SOC_DISP         Query specific parameters
  %(prog)s --ecu BMS                        Query all BMS parameters
  %(prog)s --ecu BMS --pid 2101             Query BMS PID 2101 only
  %(prog)s --raw 7E4:2101                   Raw UDS request
  %(prog)s --scan --tx 7E4 --service 21 --range 01-FF
                                            Scan PID range

  %(prog)s --wican vpn --param SOC_BMS      Use VPN address
  %(prog)s --verbose --ecu VCU              Show raw WebSocket traffic
  %(prog)s --json --param SOC_BMS           JSON output
  %(prog)s --reboot --param SOC_BMS         Query + reboot to restore AutoPID
""",
    )

    # Mode selection (mutually exclusive)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--param", nargs="+", metavar="NAME",
                      help="Query named parameters (e.g., SOC_BMS SOC_DISP)")
    mode.add_argument("--ecu", metavar="NAME",
                      help="Query all parameters for an ECU (e.g., BMS, VCU)")
    mode.add_argument("--raw", metavar="TX:PID",
                      help="Raw UDS request (e.g., 7E4:2101)")
    mode.add_argument("--scan", action="store_true",
                      help="Scan a range of PIDs (requires --tx)")

    # ECU/PID mode options
    parser.add_argument("--pid", metavar="PID",
                        help="Filter by PID code (for --ecu mode)")

    # Scan mode options
    parser.add_argument("--tx", metavar="ID",
                        help="ECU TX ID for --scan (hex, e.g., 7E4)")
    parser.add_argument("--service", metavar="SVC", default="21",
                        help="UDS service for --scan (hex, default: 21)")
    parser.add_argument("--range", metavar="START-END", default="01-FF",
                        help="PID range for --scan (hex, default: 01-FF)")

    # Connection options
    parser.add_argument("--wican", default=DEFAULT_WICAN,
                        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES.keys())} or IP (default: {DEFAULT_WICAN})")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="Response timeout in seconds (default: 3.0)")

    # Output options
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show raw WebSocket traffic and expressions")
    parser.add_argument("--reboot", action="store_true",
                        help="Reboot WiCAN after session to restore AutoPID mode")

    args = parser.parse_args()

    # Run async main
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
