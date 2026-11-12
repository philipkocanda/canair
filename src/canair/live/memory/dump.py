"""``canair memory dump``: read address ranges in checked blocks into a local dump.

The reads are :class:`~canair.live.memory.reads.BlockReader`'s. A refusal makes a gap with
its result and NRC; nothing is ever invented for it. A locked region (securityAccessDenied),
or one that refuses by silence, is left alone for the rest of its range. A skip key, Ctrl-C
or an ECU that stops answering ends the dump early; what was read is kept, and the rest is
recorded as not read.

The block follows the image's rate controller (:func:`pace`): halved by a bad reply, doubled
again after clean reads, never above the ceiling.

The dump is written as it is read: each extent of :data:`EXTENT_SIZE` bytes is appended to
the dump on disk when it finishes, so a killed process keeps everything but the extent in
flight. On a terminal, :class:`DumpMeter` draws one progress line on stderr for the whole dump.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from canair.core.terminal import terminal_columns
from canair.live.identity.software import read_software_version
from canair.memory.store import Dump, DumpWriter, Extent, Gap, pieces_hash
from canair.profile.names import make_of
from canair.render import ansi
from canair.render.formatting import duration_text, rate_text, size_text
from canair.render.glyphs import BAR_EMPTY, BAR_FULL, SEP
from canair.render.paint import Painter
from canair.render.progress import ProgressLine, Throughput, visible_length
from canair.transport.protocol import Terminal
from canair.uds.memory import MemoryFormat

from .reads import DROP, HALVING, OK, BlockReader, EcuSilent, MemoryTarget, SpanRead, maybe_session

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
    retries: int = 0
    drops: int = 0
    seconds: float = 0.0


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


def pace(reader: BlockReader, top: int, throttle_ms: int) -> None:
    """Let the image's rate controller steer ``reader``: its block back up, its throttle too.

    The reader's own halving (a lost, cut-short or too-large reply) lowers the rate as well,
    so the block stays down for :data:`~canair.memory.rate.RECOVER_AFTER` clean reads and then
    doubles step by step, never above ``top``. ``throttle_ms`` is the floor of the throttle.
    """
    # deferred: the rate controller pulls in the ELM327 frame counts, ~1 ms of parser start-up
    from canair.memory.rate import advance, start_rate

    rate = start_rate(top, throttle_ms=throttle_ms)

    def hear(event: str) -> None:
        nonlocal rate
        rate = advance(rate, event)
        if event in (HALVING, DROP):
            rate = replace(rate, block=min(rate.block, reader.block))
        if rate.throttle_ms < throttle_ms:
            rate = replace(rate, throttle_ms=throttle_ms)
        reader.block = rate.block if event == OK else min(reader.block, rate.block)
        reader.throttle_s = rate.throttle_ms / 1000

    reader.block = rate.block
    reader.on_event = hear


# -- the progress line --------------------------------------------------------------------
#
# It follows the whole dump, not the range in hand: a bar and a percentage, what is done of the
# total, the speed over the last minute and the time left, then (as the terminal allows) the
# range, the address, the block and the replies that were lost and read again. Segments that do
# not fit are left out, the least useful first, so the line never wraps; a wrapped line cannot
# be rewritten. :func:`render_line` is pure; :class:`DumpMeter` feeds it from the reader.

BAR_CELLS = 16
#: Redraw at most this often between extents, so a fast link does not flood the terminal.
MIN_REDRAW_S = 0.1
_INDENT = "  "
_JOIN = f" {SEP} "


@dataclass(frozen=True)
class LineState:
    """Everything one drawing of the line shows."""

    done: int
    total: int
    address: int
    range_index: int  # 1-based
    range_count: int
    block: int
    rate: float | None = None
    eta: float | None = None
    retried: int = 0


def _bar(fraction: float, paint: Painter) -> str:
    filled = round(fraction * BAR_CELLS)
    if 0 < fraction < 1:  # a started read shows a cell, an unfinished one leaves a gap
        filled = min(BAR_CELLS - 1, max(1, filled))
    return paint(BAR_FULL * filled, ansi.raw.BOLD) + paint(
        BAR_EMPTY * (BAR_CELLS - filled), ansi.raw.DIM
    )


def _segments(s: LineState, paint: Painter, width: int) -> dict[str, tuple[str, str]]:
    """``name -> (plain text, painted text)`` for every segment that has something to say."""
    fraction = s.done / s.total if s.total else 1.0
    where = f"0x{s.address:0{width}X}"
    if s.range_count > 1:
        where = f"range {s.range_index}/{s.range_count} {where}"
    plain = {
        "pct": (f"{100 * fraction:5.1f} %", ansi.raw.BOLD),
        "size": (f"{size_text(s.done)} / {size_text(s.total)}", ansi.raw.DIM),
        "where": (where, ansi.raw.DIM),
        "block": (f"block {s.block}", ansi.raw.DIM),
    }
    if s.rate is not None:
        plain["rate"] = (rate_text(s.rate), ansi.raw.DIM)
    if s.eta is not None:
        plain["eta"] = (f"~{duration_text(s.eta)} left", ansi.raw.DIM)
    if s.retried:
        plain["retried"] = (f"{s.retried} retried", ansi.raw.YELLOW)
    return {name: (text, paint(text, code)) for name, (text, code) in plain.items()}


#: What the line keeps, most important first, and the order it is shown in.
_PRIORITY = ("pct", "eta", "rate", "bar", "size", "retried", "where", "block")
_ORDER = ("pct", "size", "rate", "eta", "where", "block", "retried")


def render_line(s: LineState, paint: Painter, columns: int, *, address_width: int = 6) -> str:
    """The line for ``s`` in at most ``columns - 1`` columns (the last one stays free)."""
    segments = _segments(s, paint, address_width)
    fraction = s.done / s.total if s.total else 1.0

    def assemble(chosen: set[str]) -> tuple[str, str]:
        plain, painted = [], []
        for name in _ORDER:
            if name not in chosen:
                continue
            text, shown = segments[name]
            if name == "pct" and "bar" in chosen:
                bar = BAR_FULL * BAR_CELLS
                text, shown = f"{bar} {text}", f"{_bar(fraction, paint)} {shown}"
            plain.append(text)
            painted.append(shown)
        return _JOIN.join(plain), _JOIN.join(painted)

    chosen: set[str] = set()
    for name in _PRIORITY:
        if name != "bar" and name not in segments:
            continue
        plain, _ = assemble(chosen | {name})
        if len(_INDENT) + visible_length(plain) <= columns - 1 or name == "pct":
            chosen.add(name)
    return _INDENT + assemble(chosen)[1]


class DumpMeter:
    """Draws the dump's progress line from the reader's counters.

    The caller tells it where each extent starts (:meth:`extent`) and calls :meth:`show` after
    every read; between those, the bytes the reader has handed back give the position.
    """

    def __init__(
        self,
        ranges: list[tuple[int, int]],
        reader: BlockReader,
        *,
        line: ProgressLine | None = None,
        clock: Callable[[], float] = time.monotonic,
        columns: Callable[[], int] = terminal_columns,
    ):
        self._ranges = ranges
        self._reader = reader
        self._line = line or ProgressLine()
        self._paint = Painter(sys.stderr)
        self._clock = clock
        self._columns = columns
        self._sizes = [end - start + 1 for start, end in ranges]
        self.total = sum(self._sizes)
        self._before = [sum(self._sizes[:i]) for i in range(len(ranges))]
        self._width = 2 * max(3, (len(f"{max((e for _, e in ranges), default=0):X}") + 1) // 2)
        self._throughput = Throughput(clock())
        self._index = 0
        self._base = 0
        self._address = ranges[0][0] if ranges else 0
        self._delivered0 = 0
        self._drawn = float("-inf")

    @property
    def done(self) -> int:
        """Bytes accounted for: finished extents (read or refused) and what the current one gave."""
        got = max(0, self._reader.delivered - self._delivered0)
        return min(self.total, self._base + got)

    def start_range(self, index: int) -> None:
        self._index = index

    def extent(self, address: int) -> None:
        """An extent starting at ``address`` is about to be read."""
        start = self._ranges[self._index][0]
        self._address = address
        self._base = self._before[self._index] + (address - start)
        self._delivered0 = self._reader.delivered
        self.show(force=True)

    def show(self, *, force: bool = False) -> None:
        if not self._line.enabled:
            return
        now = self._clock()
        if not force and now - self._drawn < MIN_REDRAW_S:
            return
        self._drawn = now
        done = self.done
        self._throughput.observe(now, done)
        state = LineState(
            done=done,
            total=self.total,
            address=self._address + max(0, done - self._base),
            range_index=self._index + 1,
            range_count=len(self._ranges),
            block=self._reader.block,
            rate=self._throughput.rate(),
            eta=self._throughput.eta(self.total - done),
            retried=self._reader.retries,
        )
        self._line.update(
            render_line(state, self._paint, self._columns(), address_width=self._width)
        )

    def clear(self) -> None:
        self._line.clear()


async def _dump_range(
    reader: BlockReader,
    writer: DumpWriter,
    start: int,
    end: int,
    skip: Callable[[], bool],
    on_extent: Callable[[int], None],
) -> str | None:
    """Read ``start``-``end`` extent by extent; why the dump must stop, or None."""
    for lo, hi in extents_of(start, end):
        on_extent(lo)
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
    clock: Callable[[], float] = time.monotonic,
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
    pace(reader, reader.block, throttle_ms)
    meter = DumpMeter(ranges, reader, clock=clock)
    steer = reader.on_event

    def heard(event: str) -> None:
        if steer is not None:
            steer(event)
        meter.show()

    reader.on_event = heard
    began = clock()
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
                meter.start_range(len(ranges) - len(pending))
                run.stopped = await _dump_range(reader, writer, start, end, skip, meter.extent)
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
        meter.clear()
        run.rereads, run.halvings, run.retries = reader.rereads, reader.halvings, reader.retries
        run.drops, run.seconds = reader.drops, clock() - began
        writer.finish(run.stopped is None, rest_gaps(dump, pending, rest))
    return run
