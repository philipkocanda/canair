"""``Elm327Terminal`` — the transport-agnostic ELM327 protocol engine.

canair reaches an ELM327-style adapter (a WiCAN Pro's WebSocket terminal, a
direct WiFi ELM327 clone over TCP, or — future — a serial dongle) through this
one engine. It owns the whole ELM327 conversation — ELM init, ``ATSH``/``ATFCSH``
header caching, the ``>``-prompt reply accumulation, UDS ResponsePending (0x78)
handling, ``send_uds`` parsing, and the diagnostic-session + TesterPresent
keepalive — and moves bytes only through an injected
:class:`~canlib.transport.channel.Channel`. Swapping the channel is the whole of
"support a new ELM327 wire" (the "keep the WiCAN replaceable" rule): the delicate
timing loop lives here exactly once, never duplicated per transport.

It structurally satisfies :class:`canlib.transport.protocol.Terminal`, so every
live mode drives it through the shared ``dispatch_mode`` unchanged.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time

from ..log import log_command, log_response
from ..safety import enforce_command_safety
from ..timing import TimingRecorder
from ..transport_stats import TransportStats
from ..uds_parse import UdsResponse, parse_uds_response
from .channel import Channel, TcpChannel


class Elm327Terminal:
    """ELM327 protocol engine over an injected byte :class:`Channel`."""

    def __init__(
        self,
        channel: Channel,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
        hk_f1xx_offset: bool = False,
    ):
        self._channel = channel
        self.timeout = timeout
        self.verbose = verbose
        self.unsafe = unsafe
        # Profile HK F1xx -1 identity-DID quirk (tolerate 62F187 for a 22F188
        # request); forwarded to parse_uds_response's echo validation.
        self.hk_f1xx_offset = hk_f1xx_offset
        # Set when a command's read loop exits WITHOUT consuming the ELM `>`
        # prompt (a timeout mid-response): the adapter may still emit trailing
        # frames + a late prompt, which would otherwise leak into — and corrupt
        # — the next command's response. The next command drains first (see
        # _send_command_locked). This attacks the stale-frame root cause that
        # the parser's expected_sid/expected_echo validation only catches after.
        self._pipe_dirty = False
        self.elm_timeout_cmd = "ATST96"  # current ELM327 timeout command
        self._cmd_lock = asyncio.Lock()  # serialize all ELM327 commands
        # Lightweight instrumentation: total ELM commands sent and time spent
        # waiting on them. Callers (e.g. the monitor) snapshot these to report
        # per-cycle command counts / ELM latency.
        self.cmd_count = 0
        self.cmd_time = 0.0
        # Per-(ECU, PID) round-trip timing (surfaced by `canair read --timings`).
        self.timings = TimingRecorder()
        # Per-exchange outcome tally (drops/errors/decode) — the sibling of
        # `timings` read by the monitor for its live status line and stamped into
        # recorded-capture provenance.
        self.diag = TransportStats(transport=getattr(channel, "transport_name", "elm327"))
        # Optional per-ECU response budget {tx_id: seconds}. When a read passes no
        # explicit timeout, the current header's budget (else self.timeout) applies.
        self.ecu_timeouts: dict[int, float] = {}
        # Cached ELM header state so repeated set_header() for the same ECU is a
        # no-op (headers only change on ECU switch). Kept coherent for any caller
        # by inspecting commands in _send_command_locked (ATSH/ATFCSH set it,
        # ATZ/ATD/ATWS reset it).
        self._cur_header: int | None = None
        self._cur_fc_header: int | None = None

    async def connect(self):
        """Open the underlying channel and reset ELM/session state."""
        await self._channel.connect()
        self._cur_header = None
        self._cur_fc_header = None
        self._pipe_dirty = False

    async def close(self):
        """Close the underlying channel."""
        await self._channel.close()

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        """Send an ELM327 command and wait for the response.

        There are two timeout levels:
        1. ELM327 ATST timeout (~614ms at ATST96) -- how long the ELM327 chip waits
           for an ECU response before returning "NO DATA". This governs actual CAN timing.
        2. Channel timeout (this parameter) -- max wait for the ELM327 chip to reply
           over the link. Only matters if the link stalls or NRC 0x78
           (ResponsePending) extends the exchange.

        All commands are serialized via an asyncio.Lock to prevent TesterPresent
        keepalive from colliding with user commands on the single ELM327 channel.

        Args:
            cmd: ELM327 command (without CR terminator)
            timeout: channel-level timeout in seconds (default: self.timeout)

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

        # If the previous command left the pipe dirty (timed out before its ELM
        # prompt), discard any stale/late frames still buffered before sending so
        # they can't leak into this response.
        if self._pipe_dirty:
            await self._channel.drain()
            self._pipe_dirty = False

        self.cmd_count += 1
        _t0 = time.monotonic()
        await self._channel.send(cmd + "\r")

        response_parts = []
        deadline = time.monotonic() + timeout
        got_prompt = False
        # Cleared to True only when the loop exits having consumed the ELM `>`
        # prompt with no unresolved ResponsePending; any other exit (deadline,
        # partial-data early break) leaves the pipe dirty for the next command.
        clean_exit = False

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            msg = await self._channel.recv(min(remaining, 1.0))
            if msg is None:
                # Receive timeout (no data within the window).
                if got_prompt:
                    full = "".join(response_parts)
                    if "7F" in full and "78" in full:
                        clean = full.replace(" ", "").replace("\r", "").replace("\n", "")
                        if re.search(r"7F[0-9A-Fa-f]{2}78", clean):
                            continue
                    clean_exit = True
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

            response_parts.append(msg)

            full = "".join(response_parts)
            if ">" in full:
                got_prompt = True
                clean = full.replace(" ", "").replace("\r", "").replace("\n", "")
                if re.search(r"7F[0-9A-Fa-f]{2}78", clean):
                    deadline = time.monotonic() + timeout
                    continue
                clean_exit = True
                break

        # Mark the pipe dirty when we never cleanly consumed the prompt: the ELM
        # may still emit trailing frames the next command must drain first.
        self._pipe_dirty = not clean_exit

        raw = "".join(response_parts)
        raw = raw.replace(">", "").replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"[\x00-\x09\x0b-\x1f]", "", raw)

        result = raw.strip()
        log_response(cmd, result)
        elapsed = time.monotonic() - _t0
        self.cmd_time += elapsed
        # Record RTT for real UDS requests only (skip AT setup + 3E00 keepalives).
        cu = cmd.replace(" ", "").upper()
        if not cu.startswith("AT") and cu != "3E00" and self._cur_header is not None:
            self.timings.record(f"0x{self._cur_header:03X}", cu, elapsed)
        return result

    async def init_elm(self, init_string: str):
        """Send ELM327 initialization commands (``;``-separated AT commands)."""
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
        expected_echo: bytes | None = None,
        retries: int = 0,
    ) -> UdsResponse:
        """Send a UDS request and parse the response.

        Args:
            service_pid: UDS request hex string (e.g., "2101", "22C00B")
            timeout: channel-level timeout in seconds (see send_command docstring)
            expected_sid: If set, the parser validates the response echoes this SID
                (catches stale frames from previous requests).
            expected_did: If set along with ``expected_sid``, the parser also
                validates the DID echo in bytes 1..2 of the positive response.
            expected_echo: Variable-width identifier echo (see
                ``parse_uds_response``); validates the 1-byte service-21 PID or
                2-byte service-22 DID a positive response must repeat.
            retries: Re-send on a *non-answer* (timeout / NO DATA / transport
                error) up to this many extra times. A definitive negative
                response (NRC) or a positive response is returned immediately —
                only silence is retried. The Ioniq's first request after idle
                often times out (see profile note); one retry recovers it.

        Returns:
            Parsed response dict from parse_uds_response()
        """
        if timeout is None:
            # Per-ECU budget for the current header, else the client default.
            timeout = self.ecu_timeouts.get(self._cur_header, self.timeout)
        attempt = 0
        while True:
            raw = await self.send_command(service_pid, timeout=timeout)
            resp = parse_uds_response(
                raw,
                expected_sid=expected_sid,
                expected_did=expected_did,
                expected_echo=expected_echo,
                hk_f1xx_offset=self.hk_f1xx_offset,
            )
            self.diag.record_response(
                resp,
                ecu=(f"0x{self._cur_header:03X}" if self._cur_header is not None else None),
                pid=service_pid,
            )
            if resp.get("ok") or resp.get("nrc") is not None or attempt >= retries:
                return resp
            attempt += 1
            if self.verbose:
                print(
                    f"  [retry] {service_pid}: {resp.get('error', 'no response')}"
                    f" — retry {attempt}/{retries}",
                    file=sys.stderr,
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


class Elm327TcpTerminal(Elm327Terminal):
    """ELM327 engine over a direct TCP socket (transport ``elm327-tcp``).

    The counterpart to :class:`canlib.terminal.WiCANTerminal` for a generic WiFi
    ELM327 adapter (or the ELM327-Emulator's ``-n`` network mode): same engine,
    only the channel differs (a plain :class:`~canlib.transport.channel.TcpChannel`
    instead of the WiCAN WebSocket). Unlike the WiCAN, there is no HTTP config API
    and no ``reboot``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
        hk_f1xx_offset: bool = False,
    ):
        self.host = host
        self.port = port
        channel = TcpChannel(host, port, verbose=verbose)
        super().__init__(
            channel,
            timeout=timeout,
            verbose=verbose,
            unsafe=unsafe,
            hk_f1xx_offset=hk_f1xx_offset,
        )
