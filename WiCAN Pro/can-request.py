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
    Scan IOControl  python3 can-request.py --scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session
    SKM wakeup      python3 can-request.py --skm-wakeup [--level acc|ign1|ign2]
    TesterPresent   python3 can-request.py --tester-present [--target 7A5]

    Add --session to any mode (except interactive) to enter extended diagnostic
    session (10 03) before sending requests. Required for ECUs like IGPM (0x770).

    Add --wake to wake ECUs from deep sleep before entering extended session.
    Sends 10 01 (default session) as a CAN wake-up frame. Implies --session.

Requires: websockets, pyyaml (requests optional, for --reboot)
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

# Force line-buffered stdout so output appears immediately when piped
# (Python uses block buffering when stdout is not a TTY, which causes
# all output to be delayed until the buffer fills or the process exits)
if not sys.stdout.isatty():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

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

# ── Logging ───────────────────────────────────────────────────────────────
LOG_DIR = SCRIPT_DIR / "logs"

# Separate loggers for commands and responses
_cmd_logger: logging.Logger | None = None
_resp_logger: logging.Logger | None = None


def _init_logging():
    """Initialize date-stamped command and response log files.

    Creates:
        logs/commands-YYYY-MM-DD.log   — every command sent to the WiCAN
        logs/responses-YYYY-MM-DD.log  — every response received from the WiCAN
    """
    global _cmd_logger, _resp_logger

    LOG_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Command logger
    _cmd_logger = logging.getLogger("can_request.commands")
    _cmd_logger.setLevel(logging.INFO)
    _cmd_logger.propagate = False
    if not _cmd_logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"commands-{date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        _cmd_logger.addHandler(fh)

    # Response logger
    _resp_logger = logging.getLogger("can_request.responses")
    _resp_logger.setLevel(logging.INFO)
    _resp_logger.propagate = False
    if not _resp_logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"responses-{date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        _resp_logger.addHandler(fh)


def log_command(cmd: str):
    """Log a command with ISO 8601 timestamp."""
    if _cmd_logger:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _cmd_logger.info(f"[{ts}] {cmd}")


def log_response(cmd: str, response: str):
    """Log a response with ISO 8601 timestamp and the command that triggered it."""
    if _resp_logger:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        # Collapse multi-line responses to a single log line for grep-ability
        resp_oneline = response.replace("\n", " | ")
        _resp_logger.info(f"[{ts}] {cmd} -> {resp_oneline}")


# ── Safety: Dangerous Command Blocklist ──────────────────────────────────
# UDS services that can write to ECU memory, reflash firmware, or actuate
# physical outputs. These are blocked by default to prevent accidental
# damage to the vehicle's electronic control units.
#
# Blocked services:
#   0x10 02   — DiagnosticSessionControl: programming session
#   0x2E      — WriteDataByIdentifier: write arbitrary data to ECU
#   0x34      — RequestDownload: initiate ECU reflash
#   0x35      — RequestUpload: read ECU memory (can leak keys)
#   0x36      — TransferData: data transfer during flash
#   0x37      — RequestTransferExit: finalize flash transfer
#   0x38      — RequestFileTransfer
#
# Allowed services (NOT blocked):
#   0x10 01   — DiagnosticSessionControl: default session
#   0x10 03   — DiagnosticSessionControl: extended session (needed for some reads)
#   0x21/0x22 — ReadDataByIdentifier (our primary use case)
#   0x27      — SecurityAccess (needed for IOControl experiments)
#   0x2F      — InputOutputControlByIdentifier (needed for door lock/cable experiments)
#   0x31      — RoutineControl (valid diagnostic use)
#   0x3E      — TesterPresent (keepalive)
#   0x19      — ReadDTCInformation (read-only)

BLOCKED_UDS_SERVICES = {
    0x2E: "WriteDataByIdentifier (write to ECU memory)",
    0x34: "RequestDownload (ECU reflash)",
    0x35: "RequestUpload (ECU memory dump)",
    0x36: "TransferData (flash data transfer)",
    0x37: "RequestTransferExit (finalize flash)",
    0x38: "RequestFileTransfer",
}


def check_command_safety(cmd: str) -> str | None:
    """Check if a command is potentially dangerous.

    Returns an error message if the command is blocked, or None if safe.
    Checks both raw UDS hex commands and AT commands.
    """
    clean = cmd.strip().upper()

    # AT commands are generally safe — they configure the ELM327 adapter,
    # not the vehicle ECUs. Allow all AT commands.
    if clean.startswith("AT"):
        return None

    # Must be a hex UDS request. Extract the service byte.
    hex_only = clean.replace(" ", "")
    if not hex_only or not all(c in "0123456789ABCDEF" for c in hex_only):
        return None  # Not a hex command — let ELM327 handle it

    if len(hex_only) < 2:
        return None

    service_byte = int(hex_only[:2], 16)

    # Check blocklist
    if service_byte in BLOCKED_UDS_SERVICES:
        desc = BLOCKED_UDS_SERVICES[service_byte]
        return f"BLOCKED: UDS service 0x{service_byte:02X} — {desc}"

    # Special case: DiagnosticSessionControl (0x10) — block programming (02)
    # session but allow default (01) and extended (03)
    if service_byte == 0x10 and len(hex_only) >= 4:
        sub = int(hex_only[2:4], 16)
        if sub == 0x02:
            return ("BLOCKED: DiagnosticSessionControl sub 0x02 "
                    "(programmingSession) — required for flash/write operations")

    return None


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
        # Filter out ISO-TP flow control frame echoes.
        # When the ELM327 receives a multi-frame response (or sends an
        # IOControl request), it echoes the Flow Control frame it transmits.
        # These look like "F00" or "FC xx xx xx" — short hex starting with F
        # that isn't a valid UDS response (UDS positive responses use 4x-7x).
        # With headers ON, they appear as e.g. "770 F00" or "770F00".
        fc_check = line.replace(" ", "").upper()
        # Strip optional CAN ID prefix (3 hex chars)
        if len(fc_check) >= 6 and fc_check[:3].isalnum():
            fc_body = fc_check[3:]
        else:
            fc_body = fc_check
        if fc_body.startswith("F0") and len(fc_body) <= 8:
            continue  # Flow control frame echo — skip
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

    # Filter out echo of the request (e.g., "2101" or "2FBC0103" echoed back)
    # The ELM327 echoes the request before the response. We detect it by
    # checking if the first line starts with a UDS request service byte
    # (0x10-0x3F range) while the response would start with a positive
    # response SID (0x40-0x7F range) or negative response (0x7F).
    if len(data_lines) > 1:
        first = data_lines[0].replace(" ", "")
        if len(first) >= 2 and all(c in "0123456789ABCDEFabcdef" for c in first):
            first_byte = int(first[:2], 16)
            # UDS request SIDs: 0x10-0x3E (DiagnosticSessionControl through
            # various services). Response SIDs are request + 0x40 (0x50-0x7E).
            # Negative response is 0x7F.
            if 0x10 <= first_byte <= 0x3E:
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

    def __init__(self, host: str, timeout: float = 3.0, verbose: bool = False,
                 unsafe: bool = False):
        self.host = host
        self.url = f"ws://{host}/ws"
        self.timeout = timeout
        self.verbose = verbose
        self.unsafe = unsafe
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

        There are two timeout levels:
        1. ELM327 ATST timeout (~614ms at ATST96) — how long the ELM327 chip waits
           for an ECU response before returning "NO DATA". This governs actual CAN timing.
        2. WebSocket timeout (this parameter) — max wait for the ELM327 chip to reply
           over the WebSocket. Only matters if the WebSocket stalls or NRC 0x78
           (ResponsePending) extends the exchange.

        Args:
            cmd: ELM327 command (without CR terminator)
            timeout: WebSocket-level timeout in seconds (default: self.timeout)

        Returns:
            Raw response text (may contain multiple lines)

        Raises:
            ValueError: If the command is blocked by the safety check.
        """
        if timeout is None:
            timeout = self.timeout

        # Safety check — block dangerous UDS services
        blocked = check_command_safety(cmd)
        if blocked:
            if not self.unsafe:
                log_command(f"{cmd}  !! {blocked}")
                raise ValueError(blocked)
            # Unsafe mode: require explicit user consent for each command
            print(f"\n  !! WARNING: {blocked}", file=sys.stderr)
            print(f"  !! --unsafe mode is active. The user MUST be consulted and", file=sys.stderr)
            print(f"  !! must explicitly give consent before this command is executed.", file=sys.stderr)
            print(f"  !! This command can cause irreversible damage to vehicle ECUs.", file=sys.stderr)
            try:
                confirm = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("  !! Type 'YES' to execute, anything else to skip: "))
            except (EOFError, KeyboardInterrupt):
                confirm = ""
            if confirm.strip() != "YES":
                log_command(f"{cmd}  !! {blocked} — user declined")
                raise ValueError(f"Command declined by user: {cmd}")
            log_command(f"{cmd}  !! {blocked} — user confirmed (unsafe mode)")

        # Log the command
        log_command(cmd)

        # Send command with CR terminator
        await self.ws.send(cmd + "\r")
        if self.verbose:
            print(f"  [ws] Sent: {cmd!r}", file=sys.stderr)

        # Collect response until we see the > prompt or timeout
        response_parts = []
        deadline = time.monotonic() + timeout
        got_prompt = False

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=min(remaining, 0.5))
            except asyncio.TimeoutError:
                # Check if we have a complete response (prompt seen and no pending NRC)
                if got_prompt:
                    full = "".join(response_parts)
                    # If response contains 7Fxx78 (requestCorrectlyReceived-
                    # ResponsePending), the ECU is still processing — keep reading
                    if "7F" in full and "78" in full:
                        # Check specifically for the pending NRC pattern
                        clean = full.replace(" ", "").replace("\r", "").replace("\n", "")
                        if re.search(r"7F[0-9A-Fa-f]{2}78", clean):
                            continue  # Keep waiting for the real response
                    break
                if response_parts:
                    text = "".join(response_parts)
                    # Only break early if we have actual data (not just
                    # the CR echo from our command or a flow control frame echo).
                    # Strip whitespace, CRs, and check for hex content.
                    stripped = text.replace("\r", "").replace("\n", "").strip()
                    # Also filter out flow control frame echoes (F0x) which
                    # appear before the actual IOControl response
                    if stripped:
                        stripped_nfc = re.sub(r'\bF0[0-9A-Fa-f]?\b', '', stripped).strip()
                    else:
                        stripped_nfc = ""
                    if stripped_nfc and "\r" in text and "7F" not in text:
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
                got_prompt = True
                # Don't break yet — check if we have a pending NRC
                clean = full.replace(" ", "").replace("\r", "").replace("\n", "")
                if re.search(r"7F[0-9A-Fa-f]{2}78", clean):
                    # Pending NRC — keep reading for the real response
                    # Reset deadline to give ECU more time
                    deadline = time.monotonic() + timeout
                    continue
                break

        raw = "".join(response_parts)
        # Strip the prompt character and normalize line endings
        raw = raw.replace(">", "").replace("\r\n", "\n").replace("\r", "\n")
        # Strip control characters except newline
        raw = re.sub(r"[\x00-\x09\x0b-\x1f]", "", raw)

        result = raw.strip()

        # Log the response
        log_response(cmd, result)

        return result

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
            timeout: WebSocket-level timeout in seconds (see send_command docstring)

        Returns:
            Parsed response dict from parse_elm_response()
        """
        raw = await self.send_command(service_pid, timeout=timeout)
        return parse_elm_response(raw)

    async def enter_extended_session(self, wake: bool = False) -> tuple[bool, asyncio.Task | None]:
        """Enter extended diagnostic session (10 03) and start TesterPresent keepalive.

        Some ECUs (e.g. IGPM 0x770) require an extended diagnostic session before
        they will respond to 0x22 DID reads. This sends 10 03 and starts a background
        task that sends 3E 00 every 2 seconds to keep the session alive.

        Args:
            wake: If True, send a default session request (10 01) first to wake the
                ECU from deep sleep before entering extended session. The IGPM's CAN
                transceiver wakes on the 10 01 frame even though it may not respond
                in time (NO DATA). A brief delay allows the ECU to fully initialize
                before the 10 03 request.

        Returns:
            (success, tester_task) — success indicates if session was established,
            tester_task is the background keepalive task (must be cancelled by caller).
        """
        if wake:
            print(f"  Sending wake-up frame (10 01)...")
            # Wake-up needs a generous timeout — the ECU may take several seconds
            # to power up its CAN transceiver, during which the ELM327 returns
            # NO DATA. Some ECUs also send NRC 0x78 (ResponsePending) repeatedly
            # while initializing. Each pending NRC resets the deadline, so the
            # timeout value is per-attempt, not total.
            wake_resp = await self.send_uds("1001", timeout=15.0)

            if wake_resp.get("ok"):
                print(f"  ECU responded to wake-up.")
            else:
                print(f"  No response to wake-up (expected — ECU needs time to initialize).")
            # Brief delay to let the ECU fully wake up
            await asyncio.sleep(0.5)

        print(f"  Entering extended diagnostic session (10 03)...")
        resp = await self.send_uds("1003", timeout=5.0)
        if resp.get("ok"):
            print(f"  Session established.")
        elif resp.get("nrc") is not None:
            nrc = resp["nrc"]
            desc = resp["nrc_desc"]
            print(f"  WARNING: Session request returned NRC 0x{nrc:02X} ({desc})")
            print(f"  Continuing anyway — some ECUs may not need extended session.")
        else:
            error = resp.get("error", "unknown")
            print(f"  WARNING: Session request failed: {error}")
            print(f"  Continuing anyway.")

        # Start background TesterPresent task
        verbose = self.verbose

        async def _tester_present_loop():
            """Send 3E 00 every 2s to keep the extended session alive."""
            try:
                while True:
                    await asyncio.sleep(2.0)
                    try:
                        await self.send_command("3E00", timeout=1.5)
                        if verbose:
                            print(f"  [tester] 3E00 keepalive sent", file=sys.stderr)
                    except Exception:
                        pass  # Don't crash on keepalive failure
            except asyncio.CancelledError:
                pass

        tester_task = asyncio.create_task(_tester_present_loop())
        return resp.get("ok", False), tester_task


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
    print("  !skm [level]   SKM wakeup (acc/ign1/ign2/start, default: acc)")
    print("  !tester [id]   TesterPresent loop (broadcast or target ECU, Ctrl+C to stop)")
    print("  !identity      Query UDS identity DIDs from current ECU (set header first with ATSH)")
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

        if cmd.lower().startswith("!skm"):
            parts = cmd.split()
            level = parts[1] if len(parts) > 1 else "acc"
            await mode_skm_wakeup(terminal, level, verbose)
            continue

        if cmd.lower().startswith("!tester"):
            parts = cmd.split()
            target = parts[1] if len(parts) > 1 else None
            print("  Starting TesterPresent loop. Ctrl+C to stop.")
            await mode_tester_present(terminal, target, 1.0, verbose)
            continue

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

        if cmd.lower() == "!identity":
            if last_tx_id is None:
                print("  No ECU header set. Use ATSH<id> first (e.g., ATSH7A0).")
            else:
                await mode_identity(terminal, last_tx_id, session=False, wake=False,
                                    as_json=False)
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

        except ValueError as e:
            # Blocked by safety check
            print(f"  !! {e}")
        except Exception as e:
            print(f"  Error: {e}")
    print()


async def mode_param(terminal: WiCANTerminal, pids_data: dict, param_names: list[str],
                     verbose: bool, as_json: bool, session: bool = False, wake: bool = False):
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
    tester_tasks = []

    try:
        for (tx_id, pid), params in groups.items():
            ecu_name = params[0]["ecu"]

            # Set ECU header
            await terminal.set_header(tx_id)

            # Enter extended diagnostic session if requested
            if session:
                _, tester_task = await terminal.enter_extended_session(wake=wake)
                tester_tasks.append(tester_task)

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
    finally:
        for task in tester_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
                   pid_filter: str | None, verbose: bool, as_json: bool,
                   session: bool = False, wake: bool = False):
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

    # Enter extended diagnostic session if requested
    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    print(f"\n  {ecu_key} — TX 0x{tx_id:03X}")

    all_json = []

    try:
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
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

    if as_json:
        print(json.dumps(all_json, indent=2))

    print()


async def mode_raw(terminal: WiCANTerminal, raw_spec: str, verbose: bool, as_json: bool,
                   session: bool = False, hold: bool = False, wake: bool = False):
    """Send a raw UDS request specified as TX_ID:SERVICE_PID.

    Args:
        hold: If True, keep the extended diagnostic session alive after the
            command completes (TesterPresent keepalive runs until Ctrl+C).
            Useful for IOControl (2F) commands where the actuator releases
            when the session drops. Implies --session.
    """
    # Parse spec: "7E4:2101" or "7E4 2101"
    match = re.match(r"^([0-9A-Fa-f]{3})[:\s]([0-9A-Fa-f]+)$", raw_spec)
    if not match:
        print(f"  Invalid format: {raw_spec}", file=sys.stderr)
        print(f"  Expected: <TX_ID>:<SERVICE_PID>  (e.g., 7E4:2101)", file=sys.stderr)
        return

    tx_id = int(match.group(1), 16)
    service_pid = match.group(2).upper()

    # --hold and --wake imply --session
    if hold or wake:
        session = True

    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}")

    await terminal.set_header(tx_id)

    # Enter extended diagnostic session if requested
    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        response = await terminal.send_uds(service_pid)

        if as_json:
            print_json_result(response)
            if not hold:
                return
        elif not response["ok"]:
            error = response.get("error") or response.get("nrc_desc", "unknown error")
            if response.get("nrc") is not None:
                print(f"  NRC: 0x{response['nrc']:02X} — {response['nrc_desc']}")
                print(f"  Service: 0x{response.get('nrc_service', 0):02X}")
            else:
                print(f"  Error: {error}")
            if not hold:
                return
        else:
            print(f"  Response ({len(response['bytes'])} bytes): {response['hex']}")
            print()
            print_hexdump(response["bytes"])

        # Hold session open with TesterPresent until Ctrl+C
        if hold and tester_task:
            print()
            print("  Session held open (TesterPresent keepalive active).")
            print("  Press Ctrl+C to release and exit.")
            try:
                # Wait indefinitely — tester_task sends 3E00 in background
                await asyncio.Event().wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n  Releasing session...")
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass


async def mode_scan(terminal: WiCANTerminal, tx_id: int, service: int,
                    pid_range: tuple[int, int], verbose: bool, as_json: bool,
                    append_bytes: str = "", session: bool = False, wake: bool = False):
    """Scan a range of PIDs and show which respond positively.

    Args:
        append_bytes: Hex string to append after each PID (e.g. "03" for
            IOControl ShortTermAdjustment). Makes scan send e.g. "2F{DID}03".
        session: If True, enter extended diagnostic session (10 03) before
            scanning and send periodic TesterPresent (3E 00) in the background
            to keep the session alive.

    IMPORTANT — scan gently and patiently:
        - Only ONE scan at a time. The WiCAN has a single WebSocket connection;
          running a second scan in parallel will lock up the device.
        - ECUs also need time to recover between requests. Back-to-back scans
          across multiple ECUs may cause some to stop responding.
        - Use a modest --range (e.g. 01-20 before 01-FF) and check results
          before continuing. If an ECU goes silent, wait or reboot the WiCAN.
    """
    start, end = pid_range
    total = end - start + 1

    # Use 2-byte DIDs for services that take them (0x22, 0x2F, 0x31)
    wide_did = service in (0x22, 0x2F, 0x31)
    did_fmt = "04X" if wide_did else "02X"
    did_label = "DID" if wide_did else "PID"

    suffix_label = f" + suffix {append_bytes}" if append_bytes else ""
    print(f"\n  Scanning TX 0x{tx_id:03X}, service 0x{service:02X}, "
          f"{did_label}s 0x{start:{did_fmt}}..0x{end:{did_fmt}} ({total} {did_label}s){suffix_label}")

    await terminal.set_header(tx_id)

    # Enter extended diagnostic session if requested
    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    print()

    positive = []
    negative = []
    errors = []

    try:
        for pid_val in range(start, end + 1):
            # Build the request string
            req = f"{service:02X}{pid_val:{did_fmt}}{append_bytes}"

            response = await terminal.send_uds(req, timeout=2.0)

            status = ""
            if response["ok"]:
                n_bytes = len(response["bytes"])
                positive.append((pid_val, response))
                status = f"  + 0x{pid_val:{did_fmt}}: OK ({n_bytes} bytes)"
                if verbose:
                    status += f"  {response['hex']}"
                print(status)
            elif response.get("nrc") is not None:
                nrc = response["nrc"]
                desc = response["nrc_desc"]
                negative.append((pid_val, nrc, desc))
                if verbose:
                    print(f"  - 0x{pid_val:{did_fmt}}: NRC 0x{nrc:02X} ({desc})")
            else:
                error = response.get("error", "unknown")
                errors.append((pid_val, error))
                if verbose:
                    print(f"  ! 0x{pid_val:{did_fmt}}: {error}")

            # Progress indicator (non-verbose)
            if not verbose and not response["ok"]:
                # Print progress every 16 PIDs
                if (pid_val - start + 1) % 16 == 0:
                    pct = (pid_val - start + 1) / total * 100
                    print(f"  ... {pid_val - start + 1}/{total} ({pct:.0f}%)", end="\r", file=sys.stderr)
    finally:
        # Always cancel the TesterPresent background task
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
            if verbose:
                print(f"  [tester] Background keepalive stopped.", file=sys.stderr)

    print(f"\n  ─── Scan Results ────────────────────────")
    print(f"  Positive: {len(positive)}")
    print(f"  Negative: {len(negative)}")
    print(f"  Errors:   {len(errors)}")

    if positive:
        print(f"\n  Responding {did_label}s:")
        for pid_val, resp in positive:
            n = len(resp["bytes"])
            print(f"    0x{pid_val:{did_fmt}} — {n} bytes: {resp['hex'][:60]}{'...' if len(resp['hex']) > 60 else ''}")

    if as_json:
        out = {
            "tx_id": f"0x{tx_id:03X}",
            "service": f"0x{service:02X}",
            "range": f"0x{start:{did_fmt}}-0x{end:{did_fmt}}",
            "append": append_bytes if append_bytes else None,
            "session": session,
            "positive": [{"did": f"0x{p:{did_fmt}}", "bytes": r["hex"]} for p, r in positive],
            "negative": [{"did": f"0x{p:{did_fmt}}", "nrc": f"0x{n:02X}", "desc": d} for p, n, d in negative],
            "errors": [{"did": f"0x{p:{did_fmt}}", "error": e} for p, e in errors],
        }
        print(json.dumps(out, indent=2))

    print()


# ── SKM Wakeup Mode ──────────────────────────────────────────────────────

# SKM relay control DIDs
SKM_RELAYS = {
    "acc":  ("B108", "ACC (Accessory)"),
    "ign1": ("B109", "IGN1 (Ignition 1)"),
    "ign2": ("B10A", "IGN2 (Ignition 2)"),
    "start": ("B10B", "Start Relay"),
}

# Magic bytes required for SKM IOControl ON
SKM_MAGIC = "0A0A05"


# Standard UDS identity DIDs (ISO 14229-1 / Hyundai-Kia common subset).
# Ordered from most to least useful for quick identification.
IDENTITY_DIDS: list[tuple[str, str, str]] = [
    ("F190", "VIN",                     "ascii"),   # Vehicle Identification Number
    ("F188", "ECU Part Number (UDS)",  "ascii"),   # Standard UDS — may need ACC on Hyundai/Kia
    ("F187", "ECU Part Number (HK)",   "ascii"),   # Hyundai/Kia uses F187 (standard is F188)
    ("F18C", "ECU Serial / Cal ID",     "ascii"),   # Serial number / calibration ID
    ("F18B", "Manufacture Date",        "date"),    # BCD YYYYMMDD
    ("F18D", "ECU Manufacturing Date",  "date"),    # BCD alt format
    ("F191", "HW Version Number",       "ascii"),   # Hardware version
    ("F100", "Boot SW ID",              "ascii"),   # Boot software version
    ("F101", "App SW ID",               "ascii"),   # Application software version
    ("F110", "ECU Identification",      "ascii"),   # ECU name / description
    ("F17E", "SW Install Date",         "date"),    # Software installation date
    ("F18A", "System Supplier ID",      "ascii"),   # Supplier name
    ("F192", "Supplier HW Number",      "ascii"),   # Supplier hardware part number
    ("F193", "Supplier HW Version",     "ascii"),   # Supplier hardware version
    ("F194", "Supplier SW Number",      "ascii"),   # Supplier software part number
    ("F195", "Supplier SW Version",     "ascii"),   # Supplier software version
    ("F196", "Exhaust Regulation / SW", "ascii"),   # Exhaust reg info or extra SW
    ("F197", "System / Engine Name",    "ascii"),   # System or engine type name
    ("F1A0", "Diagnostic Address",      "hex"),     # Diagnostic address info
    ("F1A2", "HW Version",              "ascii"),   # Extra HW version (Hyundai)
    ("F1A4", "HW Part 2",              "ascii"),   # Extra HW version component
]


def _decode_identity_payload(payload_bytes: bytes, fmt: str) -> str:
    """Decode identity DID payload to a human-readable string."""
    # Strip trailing padding (0xAA, 0x00, 0xFF)
    stripped = payload_bytes.rstrip(b"\xaa\x00\xff")

    if not stripped:
        return "(empty)"
    if fmt == "date" and len(stripped) >= 3:
        # BCD date: common formats are YYYYMMDD (4 bytes) or YYMMDD (3 bytes)
        hex_str = stripped.hex().upper()
        if len(hex_str) == 8:   # YYYYMMDD
            return f"{hex_str[0:4]}-{hex_str[4:6]}-{hex_str[6:8]}"
        elif len(hex_str) == 6:  # YYMMDD
            return f"20{hex_str[0:2]}-{hex_str[2:4]}-{hex_str[4:6]}"
        else:
            return stripped.hex().upper()

    if fmt == "ascii":
        printable = "".join(chr(b) if 32 <= b < 127 else "." for b in stripped)
        return printable if printable else stripped.hex().upper()

    # hex or unknown
    return stripped.hex().upper()


async def mode_identity(terminal: WiCANTerminal, tx_id: int, session: bool, wake: bool,
                        as_json: bool):
    """Query standard UDS identity DIDs and OBD-II service 09 infotypes from an ECU.

    Queries the common Hyundai/Kia identity DID set (22 F1xx) and OBD-II
    service 09 vehicle info (VIN, calibration ID, ECU name), decoding
    responses as ASCII / BCD date where appropriate.
    """
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    results = []
    try:
        print(f"\n  Identity query: ECU 0x{tx_id:03X}\n")
        label_width = max(len(label) for _, label, _ in IDENTITY_DIDS)

        for i, (did_hex, label, fmt) in enumerate(IDENTITY_DIDS):
            response = await terminal.send_uds(f"22{did_hex}")
            if response["ok"]:
                payload = response["bytes"][3:]  # Strip 62 + 2-byte DID echo
                decoded = _decode_identity_payload(payload, fmt)
                raw_hex = payload.hex().upper()
                if as_json:
                    results.append({"service": "22", "did": did_hex, "label": label,
                                    "decoded": decoded, "raw": raw_hex})
                else:
                    print(f"  {did_hex}  {label:<{label_width}}  {decoded}")
                    # Show raw hex only for non-ASCII formats (e.g. dates) or
                    # when decoded contains substituted bytes (dots)
                    if fmt != "ascii" or "." in decoded:
                        print(f"        {'':>{label_width}}  raw: {raw_hex}")
            # Skip NRC — silently omit unsupported DIDs

        if as_json:
            import json
            print(json.dumps(results, indent=2))

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass


async def mode_skm_wakeup(terminal: WiCANTerminal, level: str, verbose: bool):
    """Wake sleeping ECUs via SKM relay control.

    Sends a broadcast 3E00 + 1001 to nudge the SKM awake, then establishes
    an extended diagnostic session and activates the requested relay level.

    The SKM (0x7A5) can only be reached when the CAN bus is already active
    (e.g. during charging). See skm-wakeup.md for details.
    """
    if level not in SKM_RELAYS:
        print(f"  Unknown level: {level}. Available: {', '.join(SKM_RELAYS.keys())}", file=sys.stderr)
        return False

    did, desc = SKM_RELAYS[level]

    if level == "start":
        print("  !! WARNING: Start Relay can crank the motor!", file=sys.stderr)
        print("  !! Only proceed if the car is in Park and safe conditions.", file=sys.stderr)
        try:
            confirm = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("  !! Type 'YES' to proceed: "))
        except (EOFError, KeyboardInterrupt):
            confirm = ""
        if confirm.strip() != "YES":
            print("  Aborted.")
            return False

    print(f"\n  SKM Wakeup — {desc}")
    print(f"  ─────────────────────────────────")

    # Step 1: Broadcast to wake SKM
    print(f"  [1/3] Broadcasting wake signal (3E00 + 1001 on 0x7DF)...")
    await terminal.send_command("ATSH7DF")
    await terminal.send_command("ATFCSH7DF")

    # Send multiple rounds — SKM may need several nudges to wake
    for i in range(3):
        resp = await terminal.send_command("3E00")
        if verbose:
            print(f"        3E00 [{i+1}] -> {resp}")

    resp = await terminal.send_command("1001")
    if verbose:
        print(f"        1001 -> {resp}")

    # Brief pause before switching to SKM — let CAN bus settle
    await asyncio.sleep(0.5)

    # Step 2: Extended diagnostic session on SKM
    print(f"  [2/3] Establishing extended session on SKM (0x7A5)...")
    await terminal.send_command("ATSH7A5")
    await terminal.send_command("ATFCSH7A5")

    session_ok = False
    for attempt in range(8):
        resp = await terminal.send_command("1003", timeout=3.0)
        if "50 03" in resp or "5003" in resp:
            session_ok = True
            if verbose:
                print(f"        1003 -> {resp} (attempt {attempt + 1})")
            break
        if verbose:
            print(f"        1003 -> {resp} (attempt {attempt + 1})")
        await asyncio.sleep(1.0)

    if not session_ok:
        print(f"  FAILED: SKM did not respond to extended session request.")
        print(f"  The SKM may be asleep. It only responds when the CAN bus is active")
        print(f"  (e.g. during charging). See skm-wakeup.md for details.")
        return False

    print(f"        Session established.")

    # Step 3: Send relay ON command
    # The IOControl command is 7 bytes of UDS data (2F B1 xx 03 0A 0A 05),
    # which exceeds the single-frame ISO-TP limit (max 6 data bytes after the
    # PCI byte). The ELM327 therefore sends a multi-frame request:
    #   - First Frame (FF) with the start of the payload
    #   - The ECU responds with a Flow Control (FC) frame
    #   - ELM327 sends Consecutive Frame(s) with the rest
    #
    # The ELM327 echoes "F00" (the FC frame it received from the ECU) and then
    # immediately shows the ">" prompt — BEFORE the ECU sends its actual UDS
    # response. The real response (7F2F78 pending, then 6FB10803 positive)
    # arrives asynchronously after the prompt. We must keep reading after the
    # initial "F00" response.
    await terminal._drain()
    cmd = f"2F{did}03{SKM_MAGIC}"
    print(f"  [3/3] Sending {desc} ON ({cmd})...")
    resp = await terminal.send_command(cmd, timeout=10.0)

    # If the response is just the FC echo ("F00" or similar), the real UDS
    # response hasn't arrived yet. Keep reading from the WebSocket.
    clean = resp.replace(" ", "").replace("\n", "").upper()
    is_fc_only = clean in ("F00", "FC00", "F0", "FC0") or \
                 (len(clean) <= 4 and clean.startswith("F"))

    if is_fc_only or ("7F2F78" in clean and "6F" not in clean):
        if verbose:
            reason = "FC echo" if is_fc_only else "pending NRC"
            print(f"        Initial response: {resp.strip()} ({reason})")
            print(f"        Waiting for UDS response...")
        # Keep reading WebSocket messages for the actual UDS response.
        # The ECU sends 7F2F78 (pending) first, then the positive response.
        # Collect for up to 10 seconds.
        deadline = time.monotonic() + 10.0
        extra_parts = []
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(
                    terminal.ws.recv(), timeout=min(remaining, 1.0))
                if isinstance(msg, str):
                    try:
                        parsed = json.loads(msg)
                        if parsed.get("type") == "term_out":
                            data = parsed["data"]
                            extra_parts.append(data)
                            if verbose:
                                print(f"        Recv: {data.strip()!r}")
                        continue
                    except json.JSONDecodeError:
                        pass
                    extra_parts.append(msg)
                    if verbose:
                        print(f"        Recv: {msg.strip()!r}")
                # Check if we got the positive response — stop early
                combined = "".join(extra_parts).replace(" ", "").upper()
                if "6FB1" in combined or "6F" in combined:
                    break
                # Also stop on non-pending NRC (real error)
                if re.search(r"7F2F(?!78)[0-9A-Fa-f]{2}", combined):
                    break
            except asyncio.TimeoutError:
                # If we already have a pending NRC, keep waiting
                combined = "".join(extra_parts).replace(" ", "").upper()
                if "7F2F78" in combined and "6F" not in combined:
                    continue
                break
            except Exception:
                break
        if extra_parts:
            resp = resp + "\n" + "".join(extra_parts)
            if verbose:
                print(f"        Full response: {resp.strip()}")

    # Parse response — look for positive response 6F B1 xx 03
    success = f"6F{did[0:2]}" in resp.replace(" ", "").upper() or \
              f"6FB1" in resp.replace(" ", "").upper()

    if success:
        print(f"        {desc} activated!")
        print(f"\n  Woke ECUs should now respond to queries.")
    else:
        # Check for pending + NRC
        clean = resp.replace(" ", "").upper()
        if "7F2F7F" in clean:
            print(f"        FAILED: serviceNotSupportedInActiveSession")
            print(f"        The extended session may have expired. Try again.")
        elif "7F2F78" in clean and "6F" not in clean:
            print(f"        Pending response received but no positive confirmation.")
        else:
            print(f"        Response: {resp}")
            print(f"        Could not confirm relay activation.")

    return success


# ── TesterPresent Mode ───────────────────────────────────────────────────

async def mode_tester_present(terminal: WiCANTerminal, target: str | None,
                               interval: float, verbose: bool):
    """Send TesterPresent (3E00) at regular intervals to keep a session alive.

    If no target is specified, sends to the broadcast address (7DF).
    If a target ECU TX ID is specified, sends only to that ECU.

    Runs until interrupted with Ctrl+C.
    """
    if target:
        tx_id = target.upper()
        print(f"\n  TesterPresent — targeting 0x{tx_id} every {interval:.1f}s")
        await terminal.send_command(f"ATSH{tx_id}")
        await terminal.send_command(f"ATFCSH{tx_id}")
    else:
        tx_id = "7DF"
        print(f"\n  TesterPresent — broadcast (0x7DF) every {interval:.1f}s")
        await terminal.send_command("ATSH7DF")
        await terminal.send_command("ATFCSH7DF")

    print(f"  Press Ctrl+C to stop.\n")

    count = 0
    try:
        while True:
            count += 1
            resp = await terminal.send_command("3E00", timeout=2.0)

            # Parse response
            clean = resp.replace(" ", "").replace("\n", " ").strip()
            ts = datetime.now().strftime("%H:%M:%S")

            if verbose:
                print(f"  [{ts}] #{count} 3E00 -> {clean}")
            else:
                # Count responding ECUs
                # Positive: 7E 00, Negative: 7F 3E xx
                n_pos = clean.count("7E00")
                n_neg = clean.upper().count("7F3E")
                has_nodata = "NODATA" in clean.upper().replace(" ", "")
                if has_nodata:
                    print(f"  [{ts}] #{count} NO DATA", end="\r")
                else:
                    parts = []
                    if n_pos:
                        parts.append(f"{n_pos} OK")
                    if n_neg:
                        parts.append(f"{n_neg} NRC")
                    print(f"  [{ts}] #{count} {', '.join(parts) if parts else clean[:40]}", end="\r")

            await asyncio.sleep(interval)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\n\n  Stopped after {count} messages.")


# ── Main ──────────────────────────────────────────────────────────────────

def parse_range(range_str: str) -> tuple[int, int]:
    """Parse a PID/DID range like '01-FF', 'E000-E0FF', or 'BC01-BC0B'."""
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        raise argparse.ArgumentTypeError(f"Invalid range: {range_str}. Expected format: 01-FF or E000-E0FF")
    return int(match.group(1), 16), int(match.group(2), 16)


async def async_main(args):
    """Main async entry point."""
    # Resolve WiCAN address
    host = args.wican
    if host in WICAN_ADDRESSES:
        host = WICAN_ADDRESSES[host]

    # Initialize logging
    _init_logging()
    log_command(f"--- SESSION START (host={host}, mode={'interactive' if not any([args.param, args.ecu, args.raw, args.scan, args.skm_wakeup, args.tester_present]) else 'batch'}, unsafe={args.unsafe}, session={getattr(args, 'session', False)}) ---")

    if args.unsafe:
        print("!! WARNING: --unsafe mode active. Dangerous command blocklist is bypassed.")
        print("!! Each blocked command will require explicit user consent before execution.")
        print()

    # Load YAML definitions
    pids_data = load_pids()
    init_string = pids_data.get("init", "ATSP6;ATS0;ATAL;ATST96;")

    # Create terminal connection
    terminal = WiCANTerminal(
        host=host,
        timeout=args.timeout,
        verbose=args.verbose,
        unsafe=args.unsafe,
    )

    try:
        print(f"Connecting to WiCAN at {host}...")
        await terminal.connect()
        print("Connected. Initializing ELM327...")
        await terminal.init_elm(init_string)

        # Override ELM327 ECU response timeout if requested
        if args.elm_timeout is not None:
            atst_val = max(1, min(255, round(args.elm_timeout / 4.096)))
            atst_cmd = f"ATST{atst_val:02X}"
            await terminal.send_command(atst_cmd)
            actual_ms = atst_val * 4.096
            print(f"  ELM327 timeout: {atst_cmd} ({actual_ms:.0f}ms)")

        print("Ready.")

        # --wake implies --session
        if args.wake:
            args.session = True

        # Dispatch to mode
        if args.skm_wakeup:
            await mode_skm_wakeup(terminal, args.level, args.verbose)
        elif args.tester_present:
            await mode_tester_present(terminal, args.target, args.interval, args.verbose)
        elif args.identity:
            if not args.tx:
                print("Error: --identity requires --tx (ECU TX ID)", file=sys.stderr)
                sys.exit(1)
            tx_id = int(args.tx, 16)
            await mode_identity(terminal, tx_id, session=args.session, wake=args.wake,
                                as_json=args.json)
        elif args.param:
            await mode_param(terminal, pids_data, args.param, args.verbose, args.json,
                             session=args.session, wake=args.wake)
        elif args.ecu:
            await mode_ecu(terminal, pids_data, args.ecu, args.pid, args.verbose, args.json,
                           session=args.session, wake=args.wake)
        elif args.raw:
            await mode_raw(terminal, args.raw, args.verbose, args.json,
                           session=args.session, hold=args.hold, wake=args.wake)
        elif args.scan:
            if not args.tx:
                print("Error: --scan requires --tx (ECU TX ID)", file=sys.stderr)
                sys.exit(1)
            tx_id = int(args.tx, 16)
            service = int(args.service, 16) if args.service else 0x21
            pid_range = parse_range(args.range) if args.range else (0x01, 0xFF)
            append_bytes = ""
            if args.append:
                cleaned = args.append.replace(" ", "").upper()
                if not all(c in "0123456789ABCDEF" for c in cleaned) or len(cleaned) % 2 != 0:
                    print(f"Error: --append must be valid hex bytes (e.g., 03 or 030A0A05)", file=sys.stderr)
                    sys.exit(1)
                append_bytes = cleaned
            await mode_scan(terminal, tx_id, service, pid_range, args.verbose, args.json,
                            append_bytes=append_bytes, session=args.session, wake=args.wake)
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
        log_command("--- SESSION END ---")

        # Reboot to restore AutoPID mode, or warn
        if args.reboot:
            reboot_wican(host)
        # else:
        #     print()
        #     print("NOTE: WiCAN is now in terminal mode. AutoPID is paused.")


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
  %(prog)s --scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session
                                            IOControl scan (extended session + suffix)
  %(prog)s --scan --tx 7E4 --service 22 --range BC01-BC0B
                                            Scan 0x22 DID range (auto 2-byte DIDs)

  %(prog)s --raw 770:22BC03 --session       Raw request with extended session
  %(prog)s --ecu IGPM --session             Query ECU that needs extended session
  %(prog)s --param DOOR_DRV_OPEN --session  Query parameter with extended session
  %(prog)s --raw 770:2FBC0103 --hold        IOControl: hold low beams on (Ctrl+C to release)
  %(prog)s --raw 770:2FBC0103 --hold --wake IOControl with deep sleep wake-up
  %(prog)s --ecu IGPM --session --wake      Query IGPM after waking from deep sleep

  %(prog)s --skm-wakeup                     Wake sleeping ECUs via SKM (ACC)
  %(prog)s --skm-wakeup --level ign1        Wake with IGN1 (more ECUs)
  %(prog)s --tester-present                 Send 3E00 broadcast at 1 Hz
  %(prog)s --tester-present --target 7A5    Send 3E00 to SKM only

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
                      help="Scan a range of PIDs (requires --tx). "
                           "One scan at a time only — parallel scans lock up the device. "
                           "Scan gently: use small ranges first, wait between scans.")
    mode.add_argument("--skm-wakeup", action="store_true",
                      help="Wake sleeping ECUs via SKM relay control (requires active CAN bus)")
    mode.add_argument("--tester-present", action="store_true",
                      help="Send TesterPresent (3E00) at regular intervals (Ctrl+C to stop)")
    mode.add_argument("--identity", action="store_true",
                      help="Query standard UDS identity DIDs (F100, F18x, F190, F19x) from --tx ECU "
                           "and print decoded part number, serial, manufacture date, VIN, etc.")

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
    parser.add_argument("--append", metavar="HEX",
                        help="Hex bytes to append after each DID in --scan (e.g., 03 for IOControl "
                             "ShortTermAdjustment). Makes scan send e.g. 2F{DID}03 instead of 2F{DID}.")
    parser.add_argument("--session", action="store_true",
                         help="Enter extended diagnostic session (10 03) before the request and send "
                              "periodic TesterPresent (3E 00) in the background to keep it alive. "
                              "Required for some ECUs (e.g. IGPM 0x770) that only respond to 0x22 "
                              "DID reads in extended session.")
    parser.add_argument("--hold", action="store_true",
                         help="Keep session alive after command completes (Ctrl+C to release). "
                              "Useful for IOControl (2F) commands where the actuator releases when "
                              "the diagnostic session drops. Implies --session. Only for --raw mode.")
    parser.add_argument("--wake", action="store_true",
                         help="Send a wake-up frame (10 01) before entering extended session to "
                              "rouse ECUs from deep sleep. The IGPM (0x770) goes into deep sleep "
                              "when the car is off and unplugged — this wakes it via CAN. "
                              "Implies --session.")

    # SKM wakeup options
    parser.add_argument("--level", default="acc",
                        choices=["acc", "ign1", "ign2", "start"],
                        help="Relay level for --skm-wakeup (default: acc)")

    # TesterPresent options
    parser.add_argument("--target", metavar="TX_ID",
                        help="ECU TX ID for --tester-present (hex, e.g., 7A5). Default: broadcast 7DF")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Interval in seconds for --tester-present (default: 1.0)")

    # Connection options
    parser.add_argument("--wican", default=DEFAULT_WICAN,
                        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES.keys())} or IP (default: {DEFAULT_WICAN})")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="WebSocket response timeout in seconds — max wait for ELM327 to reply (default: 3.0). "
                             "In practice, the ELM327's own ATST timeout governs how long it waits for an ECU response.")
    parser.add_argument("--elm-timeout", type=int, default=None, metavar="MS",
                        help="ELM327 ECU response timeout in milliseconds (default: ~614ms from ATST96). "
                             "Sent as ATSTxx after init. Useful for slow ECUs or scanning.")

    # Output options
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show raw WebSocket traffic and expressions")
    parser.add_argument("--reboot", action="store_true",
                        help="Reboot WiCAN after session to restore AutoPID mode")
    parser.add_argument("--unsafe", action="store_true",
                        help="Bypass dangerous command blocklist (requires explicit per-command consent)")

    args = parser.parse_args()

    # Run async main
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
