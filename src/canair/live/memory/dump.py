"""``canair memory dump``: read address ranges in checked blocks into a local dump.

The reads are :class:`~canair.live.memory.reads.BlockReader`'s. A refusal makes a gap with
its result and NRC; nothing is ever invented for it. A locked region (securityAccessDenied),
or one that refuses by silence, is left alone for the rest of its range. A skip key, Ctrl-C
or an ECU that stops answering ends the dump early; what was read is kept, and the rest is
recorded as not read.

The dump is written as it is read: each extent of :data:`EXTENT_SIZE` bytes is appended to
the dump on disk when it finishes, so a killed process keeps everything but the extent in
flight.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from canair.live.identity.software import read_software_version
from canair.memory.store import Dump, DumpWriter, Extent, Gap, pieces_hash
from canair.profile.names import make_of
from canair.render.progress import ProgressLine
from canair.transport.protocol import Terminal
from canair.uds.memory import MemoryFormat

from .reads import OK, BlockReader, EcuSilent, MemoryTarget, SpanRead, maybe_session

#: The block size when neither --block nor the ECU's max_block names one.
DEFAULT_BLOCK = 64
#: The bytes read between two writes to disk.
EXTENT_SIZE = 0x1000
SKIPPED = "skipped"
NOT_READ = "not_read"


@dataclass
class DumpRun:
    """A finished (or ended) dump and how it went."""

    dump: Dump
    stopped: str | None = None
    rereads: int = 0
    halvings: int = 0


def extents_of(start: int, end: int, size: int = EXTENT_SIZE) -> Iterator[tuple[int, int]]:
    """``start``-``end`` cut at every multiple of ``size``."""
    lo = start
    while lo <= end:
        hi = min(end, (lo // size + 1) * size - 1)
        yield lo, hi
        lo = hi + 1


def extent_record(start: int, end: int, span: SpanRead, kind: str = "full") -> Extent:
    """The record of one extent read into ``span``: its hash and what happened."""
    events = tuple(dict.fromkeys(e for e in span.events if e != OK))
    return Extent(start, end, pieces_hash(span.pieces), kind, span.reads, events)


def block_for(target: MemoryTarget, block: int | None) -> int:
    """The block to read with: ``block``, the ECU's ``max_block``, or the default.

    Raises :class:`ValueError` for a ``block`` above the ECU's recorded ``max_block``.
    """
    known = target.memory.max_block if target.memory else None
    if block is not None and known is not None and block > known:
        raise ValueError(
            f"--block {block} is above {target.name}'s max_block {known}: some ECUs stop "
            "answering after a read larger than they support. Raise max_block with "
            "`canair memory set` only once a read of that size has worked."
        )
    return block or known or DEFAULT_BLOCK


def rest_gaps(dump: Dump, ranges: list[tuple[int, int]], result: str) -> list[Gap]:
    """A ``result`` gap for each part of ``ranges`` after the last address recorded in it."""
    out = []
    for start, end in ranges:
        done = [x.end for x in (*dump.stretches, *dump.gaps) if start <= x.start <= end]
        first = max(done, default=start - 1) + 1
        if first <= end:
            out.append(Gap(first, end, result, None))
    return out


async def _dump_range(
    reader: BlockReader,
    writer: DumpWriter,
    start: int,
    end: int,
    skip: Callable[[], bool],
    progress: Callable[[str], None],
) -> str | None:
    """Read ``start``-``end`` extent by extent; why the dump must stop, or None."""
    for lo, hi in extents_of(start, end):
        progress(f"    0x{lo:X}  ({lo - start}/{end - start + 1} bytes)")
        span = await reader.read_span(lo, hi, skip)
        writer.add(span.pieces, span.gaps, extent_record(lo, hi, span))
        if span.skipped_at is not None:
            return SKIPPED
        if span.refused_rest == "unsupported":
            return "the ECU does not support ReadMemoryByAddress (NRC 0x11)"
        if span.refused_rest is not None:
            if hi < end:
                writer.add([], [Gap(hi + 1, end, span.refused_rest, span.gaps[-1].nrc)])
            return None
    return None


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

    The dump is on disk from the first extent on, and is closed whatever ends it — the last
    range, a skip, Ctrl-C or a silent ECU — so what was read is never lost.
    """
    fmt = fmt or target.format
    if fmt is None:
        raise ValueError(
            f"{target.name} has no memory format: run `canair scan memory` first, or pass --format"
        )
    size = block_for(target, block)
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
        complete=False,
    )
    run = DumpRun(dump)
    writer = DumpWriter.create(dump, store, address_width=2 * fmt.address_bytes)
    reader = BlockReader(terminal, target, fmt, size, throttle_s=throttle_ms / 1000)
    line = ProgressLine()
    pending = list(ranges)
    rest = NOT_READ
    try:
        async with maybe_session(terminal, target.tx_id, session_mode):
            dump.software = await read_software_version(
                terminal, target.tx_id, target.protocol, make=make_of(dump.profile or "")
            )
            writer.save()
            while pending and run.stopped is None:
                start, end = pending[0]
                run.stopped = await _dump_range(reader, writer, start, end, skip, line.update)
                if run.stopped is None:
                    pending.pop(0)
        if run.stopped == SKIPPED:
            rest = SKIPPED
    except EcuSilent:
        run.stopped = "the ECU stopped answering"
    except (asyncio.CancelledError, KeyboardInterrupt):
        run.stopped, rest = "interrupted", SKIPPED
        raise
    finally:
        line.clear()
        run.rereads, run.halvings = reader.rereads, reader.halvings
        writer.finish(run.stopped is None, rest_gaps(dump, pending, rest))
    return run
