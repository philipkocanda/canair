"""A fake ECU with a memory map, for the scan, dump and image tests (no device)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from canair.safety.blocklist import SessionPolicy
from canair.transport.stats import TransportStats
from canair.uds.memory import parse_memory_request
from canair.uds.parse import parse_uds_response
from canair.uds.protocol import UDS, DiagProtocol
from canair.uds.response import CAT_DROP


def address_byte(address: int) -> int:
    return address & 0xFF


@dataclass
class MemoryEcu:
    """Answers memory reads from ``regions``: ``(start, end, nrc or None)``.

    Bytes read are ``data(address)`` (``address & 0xFF`` unless given). ``max_block`` larger
    reads draw ``too_large_nrc`` (``None``: no answer at all, like a transport that cannot
    carry the reply). ``stale_once`` answers the read at that address once with the previous
    reply, as a desynchronised pipe would; ``drop_once`` cuts the reply at that address
    short once; ``lose`` answers nothing to the multi-byte read at an address that many times
    (a lost multi-frame reply from an ECU that is still up).

    The rest models what a long unattended read meets: ``silent_above`` refuses every
    address above it by not answering; ``alias`` makes the ECU ignore the address bits above
    it; ``offline`` makes it answer nothing at all (and ``offline_after`` sets it after that
    many memory reads); ``volatile`` addresses change on every read; ``software`` is what
    the version records report. Every request but a session change lands in ``sent``.
    """

    regions: list[tuple[int, int, int | None]]
    protocol: DiagProtocol = UDS
    max_block: int = 4094
    too_large_nrc: int | None = 0x14
    stale_once: set[int] = field(default_factory=set)
    drop_once: set[int] = field(default_factory=set)
    lose: dict[int, int] = field(default_factory=dict)
    hangs: bool = False  # after a read larger than max_block, never answer again
    sent: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    software: str | None = None
    data: Callable[[int], int] = address_byte
    silent_above: int | None = None
    alias: int | None = None
    offline: bool = False
    offline_after: int | None = None
    volatile: set[int] = field(default_factory=set)
    other_replies: dict[str, str] = field(default_factory=dict)
    memory_reads: int = 0
    _last: str = "7F2331"
    _hung: bool = False
    _ticks: int = 0

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def set_header(self, tx_id, **_kw):
        self.tx = tx_id

    @contextlib.asynccontextmanager
    async def short_ecu_wait(self):
        yield

    async def close(self):
        return None

    @property
    def diag(self):
        return TransportStats(transport="fake")

    @property
    def session_policy(self) -> SessionPolicy:
        return SessionPolicy()

    def _byte(self, address: int) -> int:
        if address in self.volatile:
            self._ticks += 1
            return self._ticks & 0xFF
        return self.data(address) & 0xFF

    def _memory(self, read) -> str:
        self.memory_reads += 1
        if self.offline_after is not None and self.memory_reads > self.offline_after:
            self.offline, self.offline_after = True, None
        if self.offline:
            return "NO DATA"
        if read.address in self.stale_once:
            self.stale_once.discard(read.address)
            return self._last
        if read.size > 1 and self.lose.get(read.address, 0) > 0:
            self.lose[read.address] -= 1
            return "NO DATA"
        if read.size > self.max_block:
            self._hung = self.hangs
            return "NO DATA" if self.too_large_nrc is None else f"7F23{self.too_large_nrc:02X}"
        if self.silent_above is not None and read.end > self.silent_above:
            return "NO DATA"
        address = read.address % self.alias if self.alias else read.address
        end = address + read.size - 1
        for start, last, nrc in self.regions:
            if start <= address <= last:
                if nrc is not None:
                    return f"7F23{nrc:02X}"
                if end > last:
                    return "7F2331"
                data = bytes(self._byte(a) for a in range(address, end + 1))
                return "63" + data.hex().upper()
        return "7F2331"

    def _reply(self, request: str) -> str:
        if self._hung or (self.offline and not request.startswith("23")):
            return "NO DATA"
        read = parse_memory_request(request, self.protocol)
        if read is not None:
            return self._memory(read)
        if self.software and request in ("22F189", "1A95"):
            head = "62F189" if request.startswith("22") else "5A95"
            return head + self.software.encode().hex().upper()
        if request in self.other_replies:
            return self.other_replies[request]
        return "NO DATA"

    async def send_uds(self, request, timeout=None, expected_sid=None, expected_echo=None, **_kw):
        if len(request) == 4 and request.startswith("10"):  # a session request
            self.sessions.append(request[2:])
            return parse_uds_response("50" + request[2:], expected_sid=expected_sid)
        self.sent.append(request)
        read = parse_memory_request(request, self.protocol)
        if read is not None and read.address in self.drop_once and read.size > 1:
            self.drop_once.discard(read.address)
            return {**parse_uds_response("NO DATA"), "error_kind": CAT_DROP}
        raw = self._reply(request)
        if raw.startswith("63"):
            self._last = raw
        return parse_uds_response(
            raw, expected_sid=expected_sid, expected_echo=expected_echo, request=request
        )
