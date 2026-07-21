"""SLCAN (Lawicel ASCII) CAN transport over a raw TCP socket.

The WiCAN's *SocketCAN* / *BUSMASTER* modes expose the CAN bus as an SLCAN
(Lawicel) ASCII stream on a TCP port (default 3333). python-can's bundled
``slcan`` interface only speaks to a serial port, so this module implements a
small :class:`can.BusABC` that speaks the same ASCII protocol directly over
TCP — cross-platform, no ``socat``/``slcand`` bridge required. Once you have a
bus, the whole python-can ecosystem (``Notifier``, ``Logger``, ``can-isotp``,
``udsoncan``) layers on top.

Only the subset the WiCAN uses is implemented: open/close, bitrate select,
listen-only, and standard/extended data + remote frames. Frame timestamps use
host arrival time (the device's optional ``Z1`` millisecond stamp is ignored on
parse, so framing stays unambiguous whether or not it is enabled).
"""

from __future__ import annotations

import select
import socket
import time
from collections import deque

import can

# SLCAN bitrate command table (Lawicel S0..S8).
_BITRATE_CMD = {
    10_000: "S0",
    20_000: "S1",
    50_000: "S2",
    100_000: "S3",
    125_000: "S4",
    250_000: "S5",
    500_000: "S6",
    800_000: "S7",
    1_000_000: "S8",
}


def format_slcan_frame(msg: can.Message) -> str:
    """Serialise a ``can.Message`` to an SLCAN transmit command (incl. ``\\r``)."""
    if msg.is_extended_id:
        ident = f"{msg.arbitration_id:08X}"
        kind = "R" if msg.is_remote_frame else "T"
    else:
        ident = f"{msg.arbitration_id:03X}"
        kind = "r" if msg.is_remote_frame else "t"
    dlc = msg.dlc if msg.dlc is not None else len(msg.data or b"")
    body = "" if msg.is_remote_frame else bytes(msg.data or b"").hex().upper()
    return f"{kind}{ident}{dlc:X}{body}\r"


def parse_slcan_frame(line: str, timestamp: float | None = None) -> can.Message | None:
    """Parse one SLCAN frame line into a ``can.Message`` (None if not a frame).

    ``line`` must be a single record with the trailing ``\\r`` already stripped.
    Non-frame lines (empty ACKs ``\\r``, error ``\\a``, ``V``/``N`` replies, …)
    return None. A trailing millisecond timestamp (Lawicel ``Z1``) is tolerated
    and ignored — the DLC bounds the data field, so anything after it is extra.
    """
    if not line:
        return None
    kind = line[0]
    if kind not in "tTrR":
        return None
    extended = kind in "TR"
    remote = kind in "rR"
    id_len = 8 if extended else 3
    try:
        pos = 1
        arbitration_id = int(line[pos : pos + id_len], 16)
        pos += id_len
        dlc = int(line[pos], 16)
        pos += 1
        data = b""
        if not remote:
            data = bytes.fromhex(line[pos : pos + 2 * dlc])
            if len(data) != dlc:
                return None
    except (ValueError, IndexError):
        return None
    return can.Message(
        arbitration_id=arbitration_id,
        is_extended_id=extended,
        is_remote_frame=remote,
        dlc=dlc,
        data=data,
        timestamp=timestamp if timestamp is not None else time.time(),
        is_rx=True,
    )


class SlcanTcpBus(can.BusABC):
    """python-can bus speaking SLCAN over a TCP socket (e.g. WiCAN slcan mode)."""

    def __init__(
        self,
        host: str,
        port: int = 3333,
        bitrate: int = 500_000,
        listen_only: bool = False,
        connect_timeout: float = 5.0,
        **kwargs,
    ):
        if bitrate not in _BITRATE_CMD:
            raise ValueError(
                f"Unsupported SLCAN bitrate {bitrate}; choose one of {sorted(_BITRATE_CMD)}"
            )
        self.host = host
        self.port = port
        self.channel_info = f"SLCAN/TCP {host}:{port} @ {bitrate}"
        self._buf = ""
        self._rx: deque[can.Message] = deque()

        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.setblocking(False)

        # Open the channel: close first (idempotent), set bitrate, then open in
        # active or listen-only mode.
        self._send_cmd("C")  # ensure closed before (re)configuring
        self._send_cmd(_BITRATE_CMD[bitrate])
        self._send_cmd("L" if listen_only else "O")

        super().__init__(channel=f"{host}:{port}", **kwargs)

    # -- can.BusABC ---------------------------------------------------------
    def send(self, msg: can.Message, timeout: float | None = None) -> None:
        self._write(format_slcan_frame(msg))

    def _recv_internal(self, timeout: float | None) -> tuple[can.Message | None, bool]:
        if self._rx:
            return self._rx.popleft(), False
        self._read_into_buffer(timeout)
        if self._rx:
            return self._rx.popleft(), False
        return None, False

    def shutdown(self) -> None:
        try:
            self._send_cmd("C")
        except OSError:
            pass
        try:
            self._sock.close()
        finally:
            super().shutdown()

    # -- internals ----------------------------------------------------------
    def _send_cmd(self, cmd: str) -> None:
        self._write(cmd + "\r")

    def _write(self, text: str) -> None:
        self._sock.sendall(text.encode("ascii"))

    def _read_into_buffer(self, timeout: float | None) -> None:
        """Block up to ``timeout`` for data, then drain + parse all whole lines."""
        ready, _, _ = select.select([self._sock], [], [], timeout)
        if not ready:
            return
        try:
            chunk = self._sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        if not chunk:
            raise can.CanError("SLCAN TCP connection closed by peer")
        arrival = time.time()
        self._buf += chunk.decode("ascii", errors="ignore")
        *lines, self._buf = self._buf.split("\r")
        for line in lines:
            msg = parse_slcan_frame(line, timestamp=arrival)
            if msg is not None:
                self._rx.append(msg)
