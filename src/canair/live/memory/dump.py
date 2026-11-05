"""``canair memory dump``: read address ranges in blocks into a local dump.

The block size alternates between ``N`` and ``N-1`` bytes. A memory reply repeats nothing
of its request, so the length check is its only proof; with alternating sizes, a reply
that answers the previous request always has the wrong length, is caught as stale, and
the block is read again. A block the ECU or the transport cannot deliver whole is halved.

A refusal makes a gap with its result and NRC; nothing is ever invented for it. A locked
region (securityAccessDenied) is left alone for the rest of its range. A skip key, Ctrl-C
or an ECU that stops answering ends the dump early; what was read is kept, and the rest
is recorded as not read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from canair.live.progress_line import ProgressLine
from canair.live.software_version import read_software_version
from canair.memory.store import Dump, Gap, Stretch, merge_stretches, save_dump
from canair.transport.protocol import Terminal
from canair.uds.memory import MemoryFormat, RegionResult, memory_read
from canair.uds.response import CAT_STALE

from .reads import MemoryTarget, classify, data_of, maybe_session, read_memory

#: The block size when neither --block nor the ECU's max_block names one.
DEFAULT_BLOCK = 64
#: Silent single-byte reads in a row after which the ECU is taken to have stopped answering.
SILENT_LIMIT = 3
SKIPPED = "skipped"
_STOPPED = "not_read"


class DumpStopped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class DumpRun:
    """A finished (or ended) dump and how it went."""

    dump: Dump
    stopped: str | None = None
    rereads: int = 0
    halvings: int = 0
    pieces: list[Stretch] = field(default_factory=list)


def _add_gap(gaps: list[Gap], start: int, end: int, result: str, nrc: int | None) -> None:
    if gaps and gaps[-1].result == result and gaps[-1].nrc == nrc and gaps[-1].end + 1 == start:
        gaps[-1] = Gap(gaps[-1].start, end, result, nrc)
    else:
        gaps.append(Gap(start, end, result, nrc))


class _Reader:
    def __init__(self, terminal: Terminal, target: MemoryTarget, fmt: MemoryFormat, block: int):
        self.terminal = terminal
        self.target = target
        self.fmt = fmt
        self.block = block
        self.short = False  # alternates N, N-1, N, ...
        self.silent = 0

    def next_size(self, remaining: int) -> int:
        size = self.block - 1 if self.short and self.block > 1 else self.block
        self.short = not self.short
        return max(1, min(size, remaining))

    async def read(self, address: int, size: int):
        resp = await read_memory(
            self.terminal, self.target.tx_id, memory_read(self.fmt, address, size)
        )
        return resp, classify(resp)


async def _dump_range(
    reader: _Reader,
    run: DumpRun,
    start: int,
    end: int,
    skip: Callable[[], bool],
    progress: Callable[[str], None],
    throttle_s: float,
) -> None:
    gaps = run.dump.gaps
    address = start
    while address <= end:
        if skip():
            _add_gap(gaps, address, end, SKIPPED, None)
            raise DumpStopped("skipped")
        size = reader.next_size(end - address + 1)
        progress(f"    0x{address:X}  ({address - start}/{end - start + 1} bytes)")
        resp, result = await reader.read(address, size)
        if result is not RegionResult.READABLE and resp.get("error_kind") == CAT_STALE:
            # Another request's reply, or a cut-short one: the terminal has realigned its pipe.
            run.rereads += 1
            resp, result = await reader.read(address, size)
        if throttle_s:
            await asyncio.sleep(throttle_s)
        if result is RegionResult.READABLE:
            reader.silent = 0
            run.pieces.append(Stretch(address, data_of(resp)))
            address += size
            continue
        if result in (RegionResult.TOO_LARGE, RegionResult.NO_ANSWER) and reader.block > 1:
            reader.block = max(1, size // 2)
            run.halvings += 1
            continue
        if result is RegionResult.NO_ANSWER:
            reader.silent += 1
            if reader.silent >= SILENT_LIMIT:
                _add_gap(gaps, address, end, _STOPPED, None)
                raise DumpStopped("the ECU stopped answering")
            continue
        if result is RegionResult.UNSUPPORTED:
            _add_gap(gaps, address, end, str(result), resp.get("nrc"))
            raise DumpStopped("the ECU does not support ReadMemoryByAddress (NRC 0x11)")
        if result is RegionResult.LOCKED:
            # Never unlocked, and not asked again for the rest of this range.
            _add_gap(gaps, address, end, str(result), resp.get("nrc"))
            return
        reader.silent = 0
        if size == 1:
            _add_gap(gaps, address, address, str(result), resp.get("nrc"))
            address += 1
            continue
        try:
            address += await _split_refused(reader, run, address, size, (result, resp.get("nrc")))
        except _Locked as locked:
            if locked.address > address:
                _add_gap(gaps, address, locked.address - 1, str(result), resp.get("nrc"))
            _add_gap(gaps, locked.address, end, str(RegionResult.LOCKED), locked.nrc)
            return


class _Locked(Exception):
    """A sub-read of a refused block met a locked address."""

    def __init__(self, address: int, nrc: int | None):
        super().__init__(address)
        self.address = address
        self.nrc = nrc


async def _byte_readable(reader: _Reader, address: int) -> tuple[RegionResult, int | None]:
    resp, result = await reader.read(address, 1)
    if result is RegionResult.LOCKED:
        raise _Locked(address, resp.get("nrc"))
    return result, resp.get("nrc")


async def _split_refused(
    reader: _Reader,
    run: DumpRun,
    address: int,
    size: int,
    refusal: tuple[RegionResult, int | None],
) -> int:
    """Find where readable memory starts or ends inside a refused block; bytes consumed.

    A block that straddles a hole is refused whole, so its readable bytes would be lost to
    the gap. Two 1-byte reads tell the cases apart, and a bisection finds the edge.
    """
    first, first_nrc = await _byte_readable(reader, address)
    if first is RegionResult.READABLE:
        # Readable memory ends inside the block: the longest prefix that reads, whole.
        lo, hi, data = 1, size - 1, b""
        while lo <= hi:
            mid = (lo + hi) // 2
            resp, result = await reader.read(address, mid)
            if result is RegionResult.READABLE:
                lo, data = mid + 1, data_of(resp)
            else:
                hi = mid - 1
        if not data:
            resp, _ = await reader.read(address, 1)
            data = data_of(resp)
        run.pieces.append(Stretch(address, data))
        return len(data)
    result, nrc = first, first_nrc
    last, _ = await _byte_readable(reader, address + size - 1)
    if last is not RegionResult.READABLE:
        _add_gap(run.dump.gaps, address, address + size - 1, str(refusal[0]), refusal[1])
        return size
    # The hole ends inside the block: bisect for the first readable byte.
    lo, hi = 0, size - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        r, n = await _byte_readable(reader, address + mid)
        if r is RegionResult.READABLE:
            hi = mid
        else:
            lo, result, nrc = mid, r, n
    _add_gap(run.dump.gaps, address, address + hi - 1, str(result), nrc)
    return hi


async def mode_memory_dump(
    terminal: Terminal,
    target: MemoryTarget,
    ranges: list[tuple[int, int]],
    store: Path,
    *,
    fmt: MemoryFormat | None = None,
    block: int | None = None,
    session_mode: str | None = None,
    label: str | None = None,
    throttle_ms: int = 0,
    skip: Callable[[], bool] = lambda: False,
    provenance: dict | None = None,
) -> DumpRun:
    """Read ``ranges`` of ``target`` into a new dump under ``store``; returns how it went.

    The dump is saved whatever ends it — the last range, a skip, Ctrl-C or a silent ECU —
    so what was read is never lost.
    """
    fmt = fmt or target.format
    if fmt is None:
        raise ValueError(
            f"{target.name} has no memory format: run `canair scan memory` first, or pass --format"
        )
    size = block or (target.memory.max_block if target.memory else None) or DEFAULT_BLOCK
    known = target.memory.max_block if target.memory else None
    if block is not None and known is not None and block > known:
        raise ValueError(
            f"--block {block} is above {target.name}'s max_block {known}: some ECUs stop "
            "answering after a read larger than they support. Raise max_block with "
            "`canair memory set` only once a read of that size has worked."
        )
    meta = provenance or {}
    dump = Dump(
        ecu=target.name,
        tx_id=target.tx_id,
        memory_format=fmt.name,
        taken=datetime.now().isoformat(timespec="seconds"),
        asked=list(ranges),
        label=label,
        profile=meta.get("profile"),
        variant=meta.get("variant"),
        revisions=dict(meta.get("revisions") or {}),
        canair=meta.get("canair"),
        transport=meta.get("transport"),
    )
    run = DumpRun(dump)
    reader = _Reader(terminal, target, fmt, min(size, fmt.max_size))
    line = ProgressLine()
    pending = list(ranges)
    try:
        async with maybe_session(terminal, target.tx_id, session_mode):
            dump.software = await read_software_version(terminal, target.tx_id, target.protocol)
            while pending:
                start, end = pending[0]
                await _dump_range(reader, run, start, end, skip, line.update, throttle_ms / 1000)
                pending.pop(0)
    except DumpStopped as stop:
        run.stopped = stop.reason
    except (asyncio.CancelledError, KeyboardInterrupt):
        run.stopped = "interrupted"
        _finish(run, pending, fmt, store, SKIPPED)
        raise
    finally:
        line.clear()
    _finish(run, pending, fmt, store, SKIPPED if run.stopped == "skipped" else _STOPPED)
    return run


def _finish(
    run: DumpRun, pending: list[tuple[int, int]], fmt: MemoryFormat, store: Path, rest: str
) -> None:
    """Record what was not read as ``rest``, join the pieces and save the dump (once)."""
    if run.dump.path is not None:
        return
    for start, end in pending:
        done = [x.end for x in (*run.pieces, *run.dump.gaps) if start <= x.start <= end]
        first = max(done, default=start - 1) + 1
        if first <= end:
            _add_gap(run.dump.gaps, first, end, rest, None)
    run.dump.stretches = merge_stretches(run.pieces)
    run.dump.gaps.sort(key=lambda g: g.start)
    run.dump.complete = run.stopped is None
    save_dump(run.dump, store, address_width=2 * fmt.address_bytes)
