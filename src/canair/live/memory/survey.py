"""The planning reads of ``canair memory image``: map the space, test for aliasing, survey it.

Each step reads as little as it can and leaves the decisions to :mod:`canair.memory.plan`:

1. **Map.** The readable regions the ECU's ``memory:`` section records for this software
   build, or a fresh map with the ``scan memory`` probes (silence classified, the step grown
   past a silent edge) of ``--range`` or, with none, of the format's whole space when it has
   addresses of 3 bytes or fewer. With no ``max_block`` recorded, the block search follows.
2. **Size and aliases.** The largest region's span against the common flash sizes, and a
   few sample pairs per candidate period.
3. **Survey.** A sample every stride, sorted into erased or data, and every change between
   two samples bisected to the block.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from canair.ecus.memory import MemoryRegion
from canair.memory.job import Area
from canair.memory.plan import (
    alias_candidates,
    alias_pairs,
    aliased,
    areas,
    bisect_mid,
    changes,
    flash_size,
    is_erased,
    survey_points,
)
from canair.memory.store import Stretch
from canair.uds.memory import MemoryFormat

from .reads import BlockReader, MemoryTarget
from .scan import ScanStopped, _find_max_block, _map_range, _Prober, _regions

#: The granule a change between two survey samples is bisected to.
BISECT_GRANULE = 0x40


class MapFailed(Exception):
    """The map could not be made; nothing has been read into a job yet."""


@dataclass
class Survey:
    """What the planning reads found."""

    regions: list[tuple[int, int]]
    mapped_from: str
    flash_size: int | None = None
    alias: int | None = None
    areas: list[Area] = field(default_factory=list)
    reads: int = 0
    seconds: float = 0.0

    @property
    def seconds_per_read(self) -> float:
        return self.seconds / self.reads if self.reads else 0.2


def recorded_regions(target: MemoryTarget, software: str | None) -> list[tuple[int, int]]:
    """The readable regions ``ecus/`` records for this software build (any, if unknown)."""
    if target.memory is None:
        return []
    return [
        (r.start, r.end)
        for r in target.memory.regions
        if r.readable and (software is None or r.software in (None, software))
    ]


@dataclass
class Map:
    """A fresh map: the readable regions, joined, and every region found, for ``ecus/``."""

    readable: list[tuple[int, int]]
    regions: list[MemoryRegion]


async def map_ranges(
    reader: BlockReader,
    fmt: MemoryFormat,
    ranges: list[tuple[int, int]],
    step: int,
    software: str | None,
    progress: Callable[[str], None],
) -> Map:
    """Probe ``ranges`` like ``scan memory`` and return what answers."""
    prober = _Prober(reader.terminal, reader.target, reader.throttle_s)
    points: list = []
    try:
        for start, end in ranges:
            await _map_range(prober, fmt, start, end, step, points, progress)
    except ScanStopped as stop:
        raise MapFailed(stop.reason) from None
    finally:
        reader.reads += prober.reads
    regions = _regions(points, ranges, software, complete=True)
    found: list[tuple[int, int]] = []
    for region in regions:
        if not region.readable:
            continue
        if found and found[-1][1] + 1 == region.start:
            found[-1] = (found[-1][0], region.end)
        else:
            found.append((region.start, region.end))
    if prober.last_readable is not None:
        reader.last_answered = prober.last_readable
    return Map(found, regions)


async def find_block(
    reader: BlockReader, fmt: MemoryFormat, region: tuple[int, int], limit: int
) -> int:
    """The largest read that comes back whole in ``region``: the scan's climb from 16 bytes.

    Raises :class:`MapFailed` when the ECU stops answering after a failing size.
    """
    prober = _Prober(reader.terminal, reader.target, reader.throttle_s)
    try:
        return await _find_max_block(prober, fmt, MemoryRegion(*region, "readable"), limit)
    except ScanStopped as stop:
        raise MapFailed(stop.reason) from None
    finally:
        reader.reads += prober.reads


def joined(pieces: list[Stretch]) -> bytes:
    return b"".join(p.data for p in sorted(pieces, key=lambda p: p.start))


async def sample(reader: BlockReader, address: int, size: int, end: int) -> bytes | None:
    """The bytes of one sample at ``address`` (``size``, or one less), ``None`` if refused."""
    size = reader.fresh_size(min(size, end - address + 1))
    data = joined((await reader.read_span(address, address + size - 1)).pieces)
    return data if len(data) == size else None


async def _pairs_match(reader: BlockReader, start: int, end: int, period: int, size: int):
    """The sample pairs for ``period``, up to the first that differs or is refused."""
    found: list[tuple[bytes, bytes]] = []
    for a, b in alias_pairs(start, end, period, size):
        da, db = await sample(reader, a, size, end), await sample(reader, b, size, end)
        if da is None or db is None:
            return None
        n = min(len(da), len(db))
        found.append((da[:n], db[:n]))
        if da[:n] != db[:n]:
            return None
    return found


async def find_alias(reader: BlockReader, start: int, end: int, size: int) -> int | None:
    """The smallest period the space ``start``-``end`` repeats with, or ``None``."""
    for period in alias_candidates(start, end, size):
        found = await _pairs_match(reader, start, end, period, size)
        if found is not None and aliased(found):
            return period
    return None


async def survey_region(
    reader: BlockReader,
    start: int,
    end: int,
    stride: int,
    size: int,
    progress: Callable[[str], None],
) -> list[Area]:
    """Sort ``start``-``end`` into data and erased areas, every change bisected."""
    points: dict[int, bool] = {}

    async def classify(address: int) -> bool:
        data = await sample(reader, address, size, end)
        return data is None or is_erased(data)

    addresses = survey_points(start, end, stride)
    for i, address in enumerate(addresses, 1):
        progress(f"    surveying 0x{address:X}  [{i}/{len(addresses)}]")
        points[address] = await classify(address)
    for lo, hi in changes(points):
        progress(f"    bisecting 0x{lo:X}-0x{hi:X}")
        while (mid := bisect_mid(lo, hi, BISECT_GRANULE)) is not None:
            points[mid] = await classify(mid)
            if points[mid] == points[lo]:
                lo = mid
            else:
                hi = mid
    return areas(points, start, end)


async def survey(
    reader: BlockReader,
    regions: list[tuple[int, int]],
    mapped_from: str,
    *,
    stride: int,
    size: int,
    progress: Callable[[str], None],
) -> Survey:
    """Size, aliasing and the survey of ``regions`` (already mapped)."""
    began, reads = time.monotonic(), reader.reads
    result = Survey(list(regions), mapped_from)
    if regions:
        lo, hi = max(regions, key=lambda r: r[1] - r[0])
        result.flash_size = flash_size(hi - lo + 1)
        progress("    testing for aliasing…")
        result.alias = await find_alias(reader, lo, hi, size)
        if result.alias is not None:
            result.regions = [
                (a, min(b, a + result.alias - 1)) if (a, b) == (lo, hi) else (a, b)
                for a, b in regions
            ]
    for lo, hi in result.regions:
        result.areas += await survey_region(reader, lo, hi, stride, size, progress)
    result.reads = reader.reads - reads
    result.seconds = time.monotonic() - began
    return result
