"""The ECU a memory command targets, the checked memory reads, and the session they need.

:class:`BlockReader` is the checked loop every memory command reads a span with. The block
size alternates between ``N`` and ``N-1`` bytes. A memory reply repeats nothing of its
request, so the length check is its only proof; with alternating sizes, a reply that answers
the previous request always has the wrong length, is caught as stale, and the block is read
again. A block the ECU or the transport cannot deliver whole is halved. A refused block is
split to find where readable memory starts or ends inside it.

Silence is ambiguous: an ECU that refuses an address by not answering looks like an ECU that
stopped answering. After a silent read the reader asks one byte at the last address that
answered. An answer makes the silence a refusal; no answer raises :class:`EcuSilent`, and the
command decides what that means (``memory dump`` ends, ``memory image`` pauses).

A lost reply (silence or a cut-short reply) of a size at or below the ECU's recorded
``max_block`` is not a size signal: that size has worked. The reader asks the same size once
more and halves only when that read is lost too. The Ioniq's motor controller loses about one
multi-frame reply in 40 in READY, at every size.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from canair.ecus.memory import EcuMemory, ecu_memory, protocol_of
from canair.memory.store import Gap, Stretch
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.memory import (
    MemoryFormat,
    MemoryRead,
    RegionResult,
    classify_memory_reply,
    memory_read,
)
from canair.uds.protocol import DiagProtocol
from canair.uds.response import CAT_DROP, CAT_STALE, UdsResponse
from canair.uds.services import READ_MEMORY_BY_ADDRESS

#: A memory read's reply budget. A large block takes many frames on a slow link.
READ_TIMEOUT_S = 3.0
#: Silent single-byte reads in a row, with no address known to answer, after which the ECU
#: is taken to have stopped answering.
SILENT_LIMIT = 3

#: The events a span read reports, one per read that did not simply work. ``RETRY`` is a lost
#: reply of a size the ECU is known to deliver, read again at that size.
OK, REREAD, HALVING, DROP, RETRY = "ok", "reread", "halving", "drop", "retry"


class MemoryTargetError(ValueError):
    """The ECU cannot be found, or has no memory format to read with."""


class EcuSilent(Exception):
    """The ECU did not answer a read, nor the liveness read after it."""

    def __init__(self, address: int):
        super().__init__(f"the ECU stopped answering (at 0x{address:X})")
        self.address = address


@dataclass(frozen=True)
class MemoryTarget:
    """The ECU a memory command reads: its name, address, protocol and memory section."""

    name: str
    tx_id: int
    protocol: DiagProtocol
    memory: EcuMemory | None
    ecu_def: Mapping[str, Any]

    @property
    def format(self) -> MemoryFormat | None:
        return self.memory.format if self.memory is not None else None


def resolve_target(pids_data: Mapping[str, Any], ecu: str) -> MemoryTarget:
    """The profile ECU named ``ecu`` (name, alias-free, or hex TX id)."""
    wanted = ecu.strip().upper().removeprefix("0X")
    for name, ecu_def in (pids_data.get("ecus") or {}).items():
        if not isinstance(ecu_def, Mapping) or ecu_def.get("tx_id") is None:
            continue
        tx = int(ecu_def["tx_id"])
        if name.upper() == wanted or f"{tx:X}" == wanted:
            return MemoryTarget(name, tx, protocol_of(ecu_def), ecu_memory(ecu_def), ecu_def)
    raise MemoryTargetError(f"ECU {ecu!r} is not in the profile")


async def read_memory(terminal: Terminal, tx_id: int, read: MemoryRead) -> UdsResponse:
    """Send one memory read to ``tx_id``; the reply is SID- and length-checked by the parser."""
    async with terminal.transaction():
        await terminal.set_header(tx_id)
        return await terminal.send_uds(
            read.request,
            timeout=READ_TIMEOUT_S,
            expected_sid=READ_MEMORY_BY_ADDRESS,
            expected_echo=b"",
        )


def classify(response: UdsResponse) -> RegionResult:
    """What a reply means; a reply that failed its checks counts as no answer."""
    return classify_memory_reply(response)


def data_of(response: UdsResponse) -> bytes:
    """The memory bytes of a positive reply (everything after the ``63``)."""
    return bytes(response.get("bytes") or b"")[1:]


@asynccontextmanager
async def maybe_session(terminal: Terminal, tx_id: int, mode: str | None) -> AsyncGenerator[None]:
    """Hold diagnostic session ``mode`` on ``tx_id`` (with keepalives) for the block.

    Nothing is sent when ``mode`` is None: the default session is where the ECU already is.
    """
    if mode is None:
        yield
        return
    async with SessionManager(terminal).held(tx_id, mode=mode):
        yield


@dataclass
class SpanRead:
    """What reading one span gave, and why it ended early, if it did."""

    pieces: list[Stretch] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    reads: int = 0
    events: list[str] = field(default_factory=list)
    #: ``locked``, ``no_answer`` (a refusal by silence) or ``unsupported``: the ECU will not
    #: give the rest of the range, so the caller does not ask for it.
    refused_rest: str | None = None
    skipped_at: int | None = None

    def add_gap(self, start: int, end: int, result: str, nrc: int | None) -> None:
        last = self.gaps[-1] if self.gaps else None
        if last and (last.result, last.nrc) == (result, nrc) and last.end + 1 == start:
            self.gaps[-1] = Gap(last.start, end, result, nrc)
        else:
            self.gaps.append(Gap(start, end, result, nrc))


class _Locked(Exception):
    """A sub-read of a refused block met a locked address."""

    def __init__(self, address: int, nrc: int | None):
        super().__init__(address)
        self.address = address
        self.nrc = nrc


class BlockReader:
    """Reads spans of one ECU's memory in checked blocks; its block and throttle can change.

    ``on_event`` hears every read's outcome (:data:`OK`, :data:`REREAD`, :data:`HALVING`,
    :data:`DROP`, :data:`RETRY`); the image's rate controller listens there and sets
    :attr:`block`.
    """

    def __init__(
        self,
        terminal: Terminal,
        target: MemoryTarget,
        fmt: MemoryFormat,
        block: int,
        *,
        throttle_s: float = 0.0,
        on_event: Callable[[str], None] | None = None,
    ):
        self.terminal = terminal
        self.target = target
        self.fmt = fmt
        self.block = max(1, min(block, fmt.max_size))
        self.throttle_s = throttle_s
        self.on_event = on_event
        #: The largest read the ECU is known to deliver (its ``max_block``), or None.
        self.known_block: int | None = target.memory.max_block if target.memory else None
        self.last_answered: int | None = None
        self.reads = 0
        self.rereads = 0
        self.halvings = 0
        self.retries = 0
        self.last_size = 0
        self._silent = 0
        self._retried_at: int | None = None

    def fresh_size(self, size: int) -> int:
        """``size``, or one less when the previous read had that size."""
        size = max(1, min(size, self.fmt.max_size))
        return size - 1 if size == self.last_size and size > 1 else size

    def next_size(self, remaining: int) -> int:
        return self.fresh_size(min(self.block, remaining))

    async def read(self, address: int, size: int) -> tuple[UdsResponse, RegionResult]:
        resp = await read_memory(
            self.terminal, self.target.tx_id, memory_read(self.fmt, address, size)
        )
        self.reads += 1
        self.last_size = size
        if self.throttle_s:
            await asyncio.sleep(self.throttle_s)
        result = classify(resp)
        if result is RegionResult.READABLE:
            self.last_answered = address
        return resp, result

    async def alive(self) -> bool | None:
        """Does a 1-byte read at the last address that answered still answer?

        ``None`` when no address has answered yet, so there is nothing safe to ask.
        """
        if self.last_answered is None:
            return None
        _resp, result = await self.read(self.last_answered, 1)
        return result is not RegionResult.NO_ANSWER

    def _event(self, span: SpanRead, kind: str) -> None:
        span.events.append(kind)
        if kind == REREAD:
            self.rereads += 1
        elif kind == HALVING:
            self.halvings += 1
        elif kind == RETRY:
            self.retries += 1
        if self.on_event is not None:
            self.on_event(kind)

    async def read_span(
        self, start: int, end: int, skip: Callable[[], bool] = lambda: False
    ) -> SpanRead:
        """Read ``start``-``end``; raises :class:`EcuSilent` when the ECU stops answering."""
        span = SpanRead()
        before = self.reads
        try:
            await self._read_span(span, start, end, skip)
        finally:
            span.reads = self.reads - before
        return span

    async def _read_span(
        self, span: SpanRead, start: int, end: int, skip: Callable[[], bool]
    ) -> None:
        address = start
        while address <= end:
            if skip():
                span.skipped_at = address
                return
            size = self.next_size(end - address + 1)
            resp, result = await self.read(address, size)
            if result is not RegionResult.READABLE and resp.get("error_kind") == CAT_STALE:
                # Another request's reply, or a cut-short one: the terminal has realigned.
                self._event(span, REREAD)
                resp, result = await self.read(address, size)
            if result is RegionResult.READABLE:
                self._silent = 0
                self._retried_at = None
                span.pieces.append(Stretch(address, data_of(resp)))
                self._event(span, OK)
                address += size
                continue
            if result is RegionResult.TOO_LARGE and self.block > 1:
                self.block = max(1, size // 2)
                self._event(span, HALVING)
                continue
            if result is RegionResult.NO_ANSWER:
                if not await self._silence(span, address, size, resp, end):
                    return
                continue
            if result is RegionResult.UNSUPPORTED:
                span.add_gap(address, end, str(result), resp.get("nrc"))
                span.refused_rest = str(result)
                return
            if result is RegionResult.LOCKED:
                # Never unlocked, and not asked again for the rest of this range.
                span.add_gap(address, end, str(result), resp.get("nrc"))
                span.refused_rest = str(result)
                return
            self._silent = 0
            if size == 1:
                span.add_gap(address, address, str(result), resp.get("nrc"))
                address += 1
                continue
            try:
                address += await self._split_refused(span, address, size, result, resp.get("nrc"))
            except _Locked as locked:
                if locked.address > address:
                    span.add_gap(address, locked.address - 1, str(result), resp.get("nrc"))
                span.add_gap(locked.address, end, str(RegionResult.LOCKED), locked.nrc)
                span.refused_rest = str(RegionResult.LOCKED)
                return

    async def _silence(
        self, span: SpanRead, address: int, size: int, resp: UdsResponse, end: int
    ) -> bool:
        """Handle a read nothing answered; False when the rest of the span is refused."""
        if size > 1 and resp.get("error_kind") == CAT_DROP:
            # A reply that arrived cut short: the transport lost frames, the ECU answered.
            if not self._retry(span, address, size):
                self.block = max(1, size // 2)
                self._event(span, DROP)
            return True
        alive = await self.alive()
        if alive is False:
            raise EcuSilent(address)
        if size > 1:
            if not self._retry(span, address, size):
                self.block = max(1, size // 2)
                self._event(span, HALVING)
            return True
        if alive:
            # The ECU answers elsewhere, so this address refuses by silence.
            span.add_gap(address, end, str(RegionResult.NO_ANSWER), None)
            span.refused_rest = str(RegionResult.NO_ANSWER)
            return False
        self._silent += 1
        if self._silent >= SILENT_LIMIT:
            raise EcuSilent(address)
        return True

    def _retry(self, span: SpanRead, address: int, size: int) -> bool:
        """Ask ``size`` at ``address`` once more when the ECU is known to deliver that size."""
        known = self.known_block
        if known is None or size > known or self._retried_at == address:
            return False
        self._retried_at = address
        self._event(span, RETRY)
        return True

    async def _byte_readable(self, address: int) -> tuple[RegionResult, int | None]:
        resp, result = await self.read(address, 1)
        if result is RegionResult.LOCKED:
            raise _Locked(address, resp.get("nrc"))
        return result, resp.get("nrc")

    async def _split_refused(
        self, span: SpanRead, address: int, size: int, refusal: RegionResult, nrc: int | None
    ) -> int:
        """Find where readable memory starts or ends inside a refused block; bytes consumed.

        A block that straddles a hole is refused whole, so its readable bytes would be lost
        to the gap. Two 1-byte reads tell the cases apart, and a bisection finds the edge.
        """
        first, first_nrc = await self._byte_readable(address)
        if first is RegionResult.READABLE:
            # Readable memory ends inside the block: the longest prefix that reads, whole.
            lo, hi, data = 1, size - 1, b""
            while lo <= hi:
                mid = (lo + hi) // 2
                resp, result = await self.read(address, mid)
                if result is RegionResult.READABLE:
                    lo, data = mid + 1, data_of(resp)
                else:
                    hi = mid - 1
            if not data:
                resp, _ = await self.read(address, 1)
                data = data_of(resp)
            span.pieces.append(Stretch(address, data))
            return len(data)
        result, result_nrc = first, first_nrc
        last, _ = await self._byte_readable(address + size - 1)
        if last is not RegionResult.READABLE:
            span.add_gap(address, address + size - 1, str(refusal), nrc)
            return size
        # The hole ends inside the block: bisect for the first readable byte.
        lo, hi = 0, size - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            r, n = await self._byte_readable(address + mid)
            if r is RegionResult.READABLE:
                hi = mid
            else:
                lo, result, result_nrc = mid, r, n
        span.add_gap(address, address + hi - 1, str(result), result_nrc)
        return hi
