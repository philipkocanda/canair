"""A fake ECU with a memory map, for the scan and dump tests (no device)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from canair.safety.blocklist import SessionPolicy
from canair.transport.stats import TransportStats
from canair.uds.memory import parse_memory_request
from canair.uds.parse import parse_uds_response
from canair.uds.protocol import UDS, DiagProtocol


@dataclass
class MemoryEcu:
    """Answers memory reads from ``regions``: ``(start, end, nrc or None)``.

    Bytes read are ``address & 0xFF``. ``max_block`` larger reads draw ``too_large_nrc``
    (``None``: no answer at all, like a transport that cannot carry the reply). ``stale_once``
    answers the read at that address once with the previous reply, as a desynchronised pipe
    would.
    """

    regions: list[tuple[int, int, int | None]]
    protocol: DiagProtocol = UDS
    max_block: int = 4094
    too_large_nrc: int | None = 0x14
    stale_once: set[int] = field(default_factory=set)
    hangs: bool = False  # after a read larger than max_block, never answer again
    sent: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    software: str | None = None
    _last: str = "7F2331"
    _hung: bool = False

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def set_header(self, tx_id, **_kw):
        self.tx = tx_id

    @contextlib.asynccontextmanager
    async def short_ecu_wait(self):
        yield

    @property
    def diag(self):
        return TransportStats(transport="fake")

    @property
    def session_policy(self) -> SessionPolicy:
        return SessionPolicy()

    def _reply(self, request: str) -> str:
        if self._hung:
            return "NO DATA"
        read = parse_memory_request(request, self.protocol)
        if read is None:
            if self.software and request in ("22F189", "1A95"):
                head = "62F189" if request.startswith("22") else "5A95"
                return head + self.software.encode().hex().upper()
            return "NO DATA"
        if read.address in self.stale_once:
            self.stale_once.discard(read.address)
            return self._last
        if read.size > self.max_block:
            self._hung = self.hangs
            return "NO DATA" if self.too_large_nrc is None else f"7F23{self.too_large_nrc:02X}"
        for start, end, nrc in self.regions:
            if start <= read.address <= end:
                if nrc is not None:
                    return f"7F23{nrc:02X}"
                if read.end > end:
                    return "7F2331"
                data = bytes((a & 0xFF) for a in range(read.address, read.end + 1))
                return "63" + data.hex().upper()
        return "7F2331"

    async def send_uds(self, request, timeout=None, expected_sid=None, expected_echo=None, **_kw):
        if len(request) == 4 and request.startswith("10"):  # a session request
            self.sessions.append(request[2:])
            return parse_uds_response("50" + request[2:], expected_sid=expected_sid)
        self.sent.append(request)
        raw = self._reply(request)
        if raw.startswith("63"):
            self._last = raw
        return parse_uds_response(
            raw, expected_sid=expected_sid, expected_echo=expected_echo, request=request
        )
