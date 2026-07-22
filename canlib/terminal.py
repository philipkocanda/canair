"""WiCAN WebSocket terminal connection and ELM327 session management."""

import asyncio
import json
import re
import sys
import time

try:
    import websockets
except ImportError as e:
    raise ImportError("websockets not installed. Run: pip3 install websockets") from e

try:
    import requests as _requests_mod

    HAS_REQUESTS = True
except ImportError:
    _requests_mod = None
    HAS_REQUESTS = False

from .log import log_command, log_response
from .safety import enforce_command_safety
from .uds_parse import parse_uds_response


class WiCANTerminal:
    """WebSocket connection to WiCAN in ELM327 terminal mode."""

    def __init__(
        self,
        host: str,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
    ):
        self.host = host
        self.url = f"ws://{host}/ws"
        self.timeout = timeout
        self.verbose = verbose
        self.unsafe = unsafe
        self.ws = None
        self._buffer = ""
        self.elm_timeout_cmd = "ATST96"  # current ELM327 timeout command
        self._cmd_lock = asyncio.Lock()  # serialize all ELM327 commands
        # Lightweight instrumentation: total ELM commands sent and time spent
        # waiting on them. Callers (e.g. the monitor) snapshot these to report
        # per-cycle command counts / ELM latency.
        self.cmd_count = 0
        self.cmd_time = 0.0
        # Cached ELM header state so repeated set_header() for the same ECU is a
        # no-op (headers only change on ECU switch). Kept coherent for any caller
        # by inspecting commands in _send_command_locked (ATSH/ATFCSH set it,
        # ATZ/ATD/ATWS reset it).
        self._cur_header: int | None = None
        self._cur_fc_header: int | None = None

    async def connect(self):
        """Connect to WiCAN and enter ELM327 terminal mode."""
        if self.verbose:
            print(f"  [ws] Connecting to {self.url}...", file=sys.stderr)

        self.ws = await websockets.connect(self.url, ping_interval=None)
        self._cur_header = None
        self._cur_fc_header = None

        mode_msg = json.dumps({"ws_mode": "terminal", "terminal_type": "elm327"})
        await self.ws.send(mode_msg)
        if self.verbose:
            print(f"  [ws] Sent: {mode_msg}", file=sys.stderr)

        await asyncio.sleep(0.3)
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
            except (TimeoutError, Exception):
                break

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        """Send an ELM327 command and wait for the response.

        There are two timeout levels:
        1. ELM327 ATST timeout (~614ms at ATST96) -- how long the ELM327 chip waits
           for an ECU response before returning "NO DATA". This governs actual CAN timing.
        2. WebSocket timeout (this parameter) -- max wait for the ELM327 chip to reply
           over the WebSocket. Only matters if the WebSocket stalls or NRC 0x78
           (ResponsePending) extends the exchange.

        All commands are serialized via an asyncio.Lock to prevent TesterPresent
        keepalive from colliding with user commands on the single ELM327 channel.

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

        await enforce_command_safety(cmd, self.unsafe)

        async with self._cmd_lock:
            return await self._send_command_locked(cmd, timeout)

    async def _send_command_locked(self, cmd: str, timeout: float) -> str:
        """Send command while holding the lock (internal)."""
        log_command(cmd)
        self._track_header(cmd)

        self.cmd_count += 1
        _t0 = time.monotonic()
        await self.ws.send(cmd + "\r")
        if self.verbose:
            print(f"  [ws] Sent: {cmd!r}", file=sys.stderr)

        response_parts = []
        deadline = time.monotonic() + timeout
        got_prompt = False

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=min(remaining, 1.0))
            except TimeoutError:
                if got_prompt:
                    full = "".join(response_parts)
                    if "7F" in full and "78" in full:
                        clean = full.replace(" ", "").replace("\r", "").replace("\n", "")
                        if re.search(r"7F[0-9A-Fa-f]{2}78", clean):
                            continue
                    break
                if response_parts:
                    text = "".join(response_parts)
                    stripped = text.replace("\r", "").replace("\n", "").strip()
                    if stripped:
                        stripped_nfc = re.sub(r"\bF0[0-9A-Fa-f]?\b", "", stripped).strip()
                    else:
                        stripped_nfc = ""
                    if stripped_nfc and "\r" in text and "7F" not in text:
                        # Don't early-exit if the only content is a request echo
                        # (a short hex string matching a UDS service byte 0x10-0x3E)
                        echo_only = stripped_nfc.replace(" ", "")
                        is_echo = (
                            len(echo_only) <= 8
                            and all(c in "0123456789ABCDEFabcdef" for c in echo_only)
                            and len(echo_only) >= 2
                            and 0x10 <= int(echo_only[:2], 16) <= 0x3E
                        )
                        if not is_echo:
                            break
                continue
            except websockets.exceptions.ConnectionClosed as e:
                raise ConnectionError("WebSocket connection closed") from e

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

            full = "".join(response_parts)
            if ">" in full:
                got_prompt = True
                clean = full.replace(" ", "").replace("\r", "").replace("\n", "")
                if re.search(r"7F[0-9A-Fa-f]{2}78", clean):
                    deadline = time.monotonic() + timeout
                    continue
                break

        raw = "".join(response_parts)
        raw = raw.replace(">", "").replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"[\x00-\x09\x0b-\x1f]", "", raw)

        result = raw.strip()
        log_response(cmd, result)
        self.cmd_time += time.monotonic() - _t0
        return result

    async def init_elm(self, init_string: str = "ATSP6;ATS0;ATAL;ATST96;"):
        """Send ELM327 initialization commands."""
        resp = await self.send_command("ATZ", timeout=3.0)
        if self.verbose:
            print(f"  [init] ATZ -> {resp!r}", file=sys.stderr)

        for cmd in init_string.rstrip(";").split(";"):
            cmd = cmd.strip()
            if not cmd:
                continue
            resp = await self.send_command(cmd)
            if self.verbose:
                print(f"  [init] {cmd} -> {resp!r}", file=sys.stderr)

    def _track_header(self, cmd: str) -> None:
        """Keep the cached ELM header state coherent with a command being sent.

        ATSH/ATFCSH set the (flow-control) header; ATZ/ATWS/ATD reset ELM
        defaults (clearing the header). A malformed header sets the cache to
        None so the next set_header() re-sends. This runs for *every* command,
        so direct sends (e.g. skm_wakeup) can't desync the cache.
        """
        cu = cmd.upper().replace(" ", "")

        def _hx(s: str) -> int | None:
            try:
                return int(s, 16)
            except ValueError:
                return None

        if cu.startswith("ATFCSH"):
            self._cur_fc_header = _hx(cu[6:])
        elif cu.startswith("ATSH"):
            self._cur_header = _hx(cu[4:])
        elif cu in ("ATZ", "ATWS", "ATD"):
            self._cur_header = None
            self._cur_fc_header = None

    async def set_header(self, tx_id: int):
        """Set the ELM327 header (target ECU TX ID).

        Cached: the ATSH/ATFCSH pair is skipped when the header is already set
        to ``tx_id``, so polling many PIDs on one ECU (or re-selecting the same
        ECU across cycles) costs zero header round-trips after the first.
        """
        hex_id = f"{tx_id:03X}"
        if self._cur_header != tx_id:
            resp = await self.send_command(f"ATSH{hex_id}")
            if self.verbose:
                print(f"  [header] ATSH{hex_id} -> {resp!r}", file=sys.stderr)

        if self._cur_fc_header != tx_id:
            resp = await self.send_command(f"ATFCSH{hex_id}")
            if self.verbose:
                print(f"  [header] ATFCSH{hex_id} -> {resp!r}", file=sys.stderr)

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_did: int | None = None,
    ) -> dict:
        """Send a UDS request and parse the response.

        Args:
            service_pid: UDS request hex string (e.g., "2101", "22C00B")
            timeout: WebSocket-level timeout in seconds (see send_command docstring)
            expected_sid: If set, parser validates the response echoes this SID
                (catches stale frames from previous requests).
            expected_did: If set along with ``expected_sid``, parser also
                validates the DID echo in bytes 1..2 of the positive response.

        Returns:
            Parsed response dict from parse_uds_response()
        """
        raw = await self.send_command(service_pid, timeout=timeout)
        return parse_uds_response(
            raw,
            expected_sid=expected_sid,
            expected_did=expected_did,
        )

    async def enter_extended_session(
        self, wake: bool = False, mode: str = "03"
    ) -> tuple[bool, asyncio.Task | None]:
        """Enter a diagnostic session (default 10 03) and start TesterPresent keepalive.

        Args:
            wake: If True, send a default session request (10 01) first to wake the
                ECU from deep sleep before entering the session.
            mode: DiagnosticSessionControl sub-function (hex, no 0x). Default
                ``"03"`` (UDS extendedDiagnosticSession); use ``"81"`` for the
                KWP2000 standardDiagnosticSession on ECUs that reject 10 03.

        Returns:
            (success, tester_task) -- success indicates if session was established,
            tester_task is the background keepalive task (must be cancelled by caller).
        """
        mode = mode.upper().removeprefix("0X").zfill(2)
        req = f"10{mode}"
        if wake:
            # Use fast timeout during wake — some ECUs (SKM) have a ~2s sleep
            # timer and need rapid CAN traffic to stay awake
            await self.send_command("ATST10")  # 64ms
            wake_resp = await self.send_uds("1001", timeout=3.0)
            if not wake_resp.get("ok"):
                # First frame may just trigger the transceiver — retry
                wake_resp = await self.send_uds("1001", timeout=3.0)
            if wake_resp.get("ok"):
                print("  Wake-up: ECU responded.")
            await self.send_command(self.elm_timeout_cmd)  # restore

        resp = await self.send_uds(req, timeout=5.0)
        if resp.get("ok"):
            print(f"  Session (10 {mode}) established.")
        elif resp.get("nrc") is not None:
            nrc = resp["nrc"]
            desc = resp["nrc_desc"]
            print(f"  WARNING: Session request returned NRC 0x{nrc:02X} ({desc})")
            print("  Continuing anyway -- some ECUs may not need extended session.")
        else:
            error = resp.get("error", "unknown")
            print(f"  Session request failed: {error} — retrying in 0.5s...")
            await asyncio.sleep(0.5)
            resp = await self.send_uds(req, timeout=5.0)
            if resp.get("ok"):
                print(f"  Session (10 {mode}) established (on retry).")
            elif resp.get("nrc") is not None:
                nrc = resp["nrc"]
                desc = resp["nrc_desc"]
                print(f"  WARNING: Session retry returned NRC 0x{nrc:02X} ({desc})")
                print("  Continuing anyway.")
            else:
                error2 = resp.get("error", "unknown")
                print(f"  WARNING: Session retry also failed: {error2}")
                print("  Continuing anyway.")

        verbose = self.verbose

        async def _tester_present_loop():
            """Send 3E 00 every 2s to keep the extended session alive."""
            try:
                while True:
                    await asyncio.sleep(2.0)
                    try:
                        await self.send_command("3E00", timeout=1.5)
                        if verbose:
                            print("  [tester] 3E00 keepalive sent", file=sys.stderr)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass

        tester_task = asyncio.create_task(_tester_present_loop())
        return resp.get("ok", False), tester_task


def reboot_wican(host: str):
    """Reboot WiCAN device via HTTP POST to restore AutoPID mode."""
    if not HAS_REQUESTS:
        print(
            "  Cannot reboot: 'requests' module not installed. Run: pip3 install requests",
            file=sys.stderr,
        )
        print(
            f"  Manual reboot: curl -X POST http://{host}/system_reboot -d reboot",
            file=sys.stderr,
        )
        return False

    url = f"http://{host}/system_reboot"
    try:
        resp = _requests_mod.post(url, data="reboot", timeout=5)
        print(f"Rebooting WiCAN... ({resp.status_code})")
        return True
    except _requests_mod.RequestException as e:
        print(f"  FAILED to reboot: {e}", file=sys.stderr)
        return False
