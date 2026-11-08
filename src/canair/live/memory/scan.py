"""``canair scan memory``: map which address ranges an ECU answers memory reads in.

The scan reads as little as it can: one byte at every ``step`` across the ranges the user
named, then one byte at a time to bisect each change between two probes to its exact
address. Only inside a region that answered does it read more, to find the largest block
the ECU and the transport deliver (``max_block``), climbing from small sizes to at most
:data:`DEFAULT_BLOCK_LIMIT` unless told otherwise: some ECUs hang on a read larger than they
support. Every read is ReadMemoryByAddress.

Silence is classified. After a probe nothing answers, one byte is read at the last address
that did: an answer makes the silent address a refusal (``no_answer``), and the map goes on.
Above the last readable address the probes then step to the next boundary the silent address
is aligned to, so a large silent space costs a few timeouts, not hundreds.

It stops, and reports why, when the ECU does not support the service, answers
securityAccessDenied (canair never unlocks), answers conditionsNotCorrect to every read
(wrong session or vehicle state), or stops answering.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path

from canair.ecus.memory import MemoryRegion
from canair.live.identity.software import read_software_version
from canair.profile.model import Profile
from canair.profile.names import make_of
from canair.render.progress import ProgressLine
from canair.transport.protocol import Terminal
from canair.uds.memory import (
    MAX_READ_BYTES,
    PROBE_ALFIDS,
    MemoryFormat,
    MemoryRequestError,
    RegionResult,
    format_from_alfid,
    memory_read,
)
from canair.yaml_edit.memory import record_scan

from .reads import SILENT_LIMIT, MemoryTarget, classify, maybe_session, read_memory

#: conditionsNotCorrect reads in a row, with nothing else seen, after which the scan stops.
CONDITIONS_LIMIT = 8

_IGNORED_BY_FORMAT = frozenset(
    {RegionResult.BAD_FORMAT, RegionResult.NO_ANSWER, RegionResult.REFUSED}
)


class ScanStopped(Exception):
    """A stop rule fired; ``reason`` says which, ``at`` where."""

    def __init__(
        self,
        reason: str,
        at: int | None = None,
        result: RegionResult | None = None,
        nrc: int | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.at = at
        self.point = (at, result, nrc) if at is not None and result is not None else None


@dataclass(frozen=True)
class FormatProbe:
    """What one ALFID's 1-byte read at the first address answered."""

    format: str
    result: RegionResult
    nrc: int | None


@dataclass
class MemoryScan:
    """What ``scan memory`` found."""

    ecu: str
    tx_id: int
    format: MemoryFormat | None
    software: str | None = None
    regions: list[MemoryRegion] = field(default_factory=list)
    max_block: int | None = None
    format_probes: list[FormatProbe] = field(default_factory=list)
    stopped: str | None = None
    reads: int = 0
    written: Path | None = None


class _Prober:
    """Sends the 1-byte probes, counts them and applies the stop rules."""

    def __init__(self, terminal: Terminal, target: MemoryTarget, throttle_s: float):
        self.terminal = terminal
        self.target = target
        self.throttle_s = throttle_s
        self.reads = 0
        self.last_readable: int | None = None
        self._silent = 0
        self._conditions = 0
        self._anything_else = False

    async def ask(
        self, fmt: MemoryFormat, address: int, size: int = 1
    ) -> tuple[RegionResult, int | None]:
        read = memory_read(fmt, address, size)
        resp = await read_memory(self.terminal, self.target.tx_id, read)
        self.reads += 1
        if self.throttle_s:
            await asyncio.sleep(self.throttle_s)
        return classify(resp), resp.get("nrc")

    async def _alive(self, fmt: MemoryFormat) -> bool | None:
        """Does the last readable address still answer? ``None``: none is known yet."""
        if self.last_readable is None:
            return None
        result, _ = await self.ask(fmt, self.last_readable)
        return result is not RegionResult.NO_ANSWER

    async def probe(self, fmt: MemoryFormat, address: int) -> tuple[RegionResult, int | None]:
        result, nrc = await self.ask(fmt, address)
        if result is RegionResult.READABLE:
            self.last_readable = address
        if result is RegionResult.NO_ANSWER:
            alive = await self._alive(fmt)
            if alive is False:
                raise ScanStopped("the ECU stopped answering", address)
            self._silent = 0 if alive else self._silent + 1
        else:
            self._silent = 0
        if result is RegionResult.CONDITIONS:
            self._conditions += 1
        elif result is not RegionResult.NO_ANSWER:
            self._anything_else = True
        if result is RegionResult.UNSUPPORTED:
            raise ScanStopped("the ECU does not support ReadMemoryByAddress (NRC 0x11)", address)
        if result is RegionResult.LOCKED:
            raise ScanStopped(
                "securityAccessDenied: this region needs security access, which canair does "
                "not provide",
                address,
                result,
                nrc,
            )
        if result is RegionResult.WRONG_SESSION:
            raise ScanStopped("not in this session: run again with --session", address)
        if self._silent >= SILENT_LIMIT:
            raise ScanStopped("the ECU stopped answering", address)
        if self._conditions >= CONDITIONS_LIMIT and not self._anything_else:
            raise ScanStopped(
                "conditionsNotCorrect on every read: wrong session or vehicle state", address
            )
        return result, nrc


async def _find_format(prober: _Prober, address: int, scan: MemoryScan) -> MemoryFormat:
    """Try the usual ALFIDs with a 1-byte read of ``address``; raise if none is clear."""
    candidates: list[MemoryFormat] = []
    for alfid in PROBE_ALFIDS:
        fmt = format_from_alfid(alfid)
        if address > fmt.max_address:
            continue
        result, nrc = await prober.ask(fmt, address)
        scan.format_probes.append(FormatProbe(fmt.name, result, nrc))
        if result is RegionResult.UNSUPPORTED:
            raise ScanStopped("the ECU does not support ReadMemoryByAddress (NRC 0x11)", address)
        if result is RegionResult.READABLE:
            return fmt
        if result not in _IGNORED_BY_FORMAT:
            candidates.append(fmt)
    if len(candidates) == 1:
        return candidates[0]
    raise ScanStopped(
        "the format probes cannot tell a wrong format from a wrong address; pass --format "
        "(e.g. 24 for a 4-byte address and 2-byte size)",
        address,
    )


Point = tuple[int, RegionResult, int | None]


async def _map_range(
    prober: _Prober,
    fmt: MemoryFormat,
    start: int,
    end: int,
    step: int,
    points: list[Point],
    progress: Callable[[str], None],
) -> None:
    """Probe ``start..end`` every ``step`` bytes, then bisect every change to its address.

    Past a silent refusal above every readable address, the step grows to the alignment of
    the silent address (``0x400000`` → probes at ``0x800000``, ``0xC00000``).
    """
    address, n = start, 0
    while True:
        n += 1
        progress(f"    probing 0x{address:X}  [{n}]")
        result, nrc = await prober.probe(fmt, address)
        points.append((address, result, nrc))
        top = prober.last_readable
        if result is RegionResult.NO_ANSWER and top is not None and address > top:
            step = max(step, address & -address)
        if address >= end:
            break
        address = min(end, address + step)
    while True:
        points.sort(key=lambda p: p[0])
        pair = next(
            (
                (a, b)
                for a, b in pairwise(points)
                if a[1] != b[1] and b[0] - a[0] > 1 and start <= a[0] and b[0] <= end
            ),
            None,
        )
        if pair is None:
            return
        (lo, lo_result, _), (hi, _, _) = pair
        while hi - lo > 1:
            mid = (lo + hi) // 2
            progress(f"    bisecting 0x{lo:X}-0x{hi:X}")
            result, nrc = await prober.probe(fmt, mid)
            points.append((mid, result, nrc))
            if result == lo_result:
                lo = mid
            else:
                hi = mid


def _regions(
    points: list[Point], ranges: list[tuple[int, int]], software: str | None, *, complete: bool
) -> list[MemoryRegion]:
    """Join the probed points into regions, each ending where the next result starts.

    A range's last region runs to the range's end, or, for a scan that stopped early, to
    the last address probed: nothing past it is claimed.
    """
    today = date.today().isoformat()
    out: list[MemoryRegion] = []

    def region(first: Point, last_address: int) -> MemoryRegion:
        address, result, nrc = first
        readable = result is RegionResult.READABLE
        return MemoryRegion(
            address,
            last_address,
            str(result),
            None if readable else nrc,
            software if readable else None,
            today,
        )

    for start, end in ranges:
        inside = sorted({p[0]: p for p in points if start <= p[0] <= end}.values())
        if not inside:
            continue
        current = inside[0]
        for point in inside[1:]:
            if point[1] != current[1]:
                out.append(region(current, point[0] - 1))
                current = point
        out.append(region(current, end if complete else inside[-1][0]))
    return out


#: The largest block the scan tries unless --block-limit says otherwise. Some ECUs hang on a
#: read larger than they support (the Ioniq's climate unit stops answering diagnostics until
#: its supply is cycled after a 192-byte read), so the search stays at sizes seen to be safe.
DEFAULT_BLOCK_LIMIT = 128
#: The first block size the search tries; each step doubles it.
_FIRST_BLOCK = 16


async def _find_max_block(
    prober: _Prober, fmt: MemoryFormat, region: MemoryRegion, limit: int
) -> int:
    """The largest read at ``region.start`` that comes back whole, climbing from small sizes.

    The sizes double up to ``limit`` and the search stops at the first that fails, so it
    asks for at most one size the ECU cannot deliver. After a failure a 1-byte read checks
    that the ECU still answers; one that does not stops the scan with a warning.
    """
    top = min(fmt.max_size, region.end - region.start + 1, MAX_READ_BYTES, limit)
    best, size = 1, min(_FIRST_BLOCK, top)
    while True:
        result, _ = await prober.ask(fmt, region.start, size)
        if result is not RegionResult.READABLE:
            alive, _ = await prober.ask(fmt, region.start, 1)
            if alive is RegionResult.NO_ANSWER:
                raise ScanStopped(
                    f"the ECU stopped answering after a {size}-byte read. Some ECUs hang on "
                    "a read larger than they support; cycle its supply (ignition or charging "
                    "off and on) to bring it back",
                    region.start,
                )
            return best
        best = size
        if size >= top:
            return best
        size = min(size * 2, top)


async def mode_memory_scan(
    terminal: Terminal,
    target: MemoryTarget,
    ranges: list[tuple[int, int]],
    *,
    step: int = 0x1000,
    fmt: MemoryFormat | None = None,
    session_mode: str | None = None,
    throttle_ms: int = 20,
    find_block: bool = True,
    block_limit: int = DEFAULT_BLOCK_LIMIT,
    write_yaml: bool = True,
    profile: Profile,
) -> MemoryScan:
    """Map ``ranges`` on ``target``; record the regions in its ``memory:`` section."""
    fmt = fmt or target.format
    scan = MemoryScan(target.name, target.tx_id, fmt)
    prober = _Prober(terminal, target, throttle_ms / 1000.0)
    line = ProgressLine()
    points: list[Point] = []
    try:
        async with maybe_session(terminal, target.tx_id, session_mode):
            scan.software = await read_software_version(
                terminal, target.tx_id, target.protocol, make=make_of(profile.name)
            )
            if scan.format is None:
                line.update("    finding the memory format…")
                scan.format = await _find_format(prober, ranges[0][0], scan)
            for start, end in ranges:
                await _map_range(prober, scan.format, start, end, step, points, line.update)
            scan.regions = _regions(points, ranges, scan.software, complete=True)
            readable = [r for r in scan.regions if r.readable]
            if find_block and readable:
                line.update("    finding the largest block…")
                biggest = max(readable, key=lambda r: r.end - r.start)
                scan.max_block = await _find_max_block(prober, scan.format, biggest, block_limit)
    except ScanStopped as stop:
        scan.stopped = stop.reason + (f" (at 0x{stop.at:X})" if stop.at is not None else "")
        if stop.point is not None:
            points.append(stop.point)
        if scan.format is not None:
            scan.regions = _regions(points, ranges, scan.software, complete=False)
    except MemoryRequestError as e:
        scan.stopped = str(e)
    finally:
        line.clear()
    scan.reads = prober.reads
    if write_yaml and scan.format is not None and scan.regions:
        scan.written = record_scan(
            target.tx_id, scan.regions, fmt=scan.format, max_block=scan.max_block, profile=profile
        )
    return scan
