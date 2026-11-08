"""Reading a memory image job's extents: the rate, the checked re-reads, pause and continuity.

:class:`JobReader` holds what one run of a job reads with — its link, its block reader and
rate, and the dump's writer — and the steps over them. :func:`JobReader.guarded` is the one
place a silent ECU, a failed check or a dropped link turns into a pause
(:func:`~canair.live.memory.liveness.wait_until_ready`), after which the step runs again.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from canair.memory.job import FULL, SAMPLE, JobPlan, PlannedExtent, continuity_extents
from canair.memory.job import missing_extents as missing
from canair.memory.plan import is_erased
from canair.memory.rate import Rate, advance, start_rate
from canair.memory.store import DumpWriter, Extent, Stretch, pieces_hash
from canair.memory.verify import (
    FIRST_READ_ERROR,
    REREAD_ERROR,
    SAME,
    VOLATILE,
    recheck_starts,
    settle,
)
from canair.render.progress import ProgressLine
from canair.transport.errors import transport_error_types

from .dump import extent_record
from .liveness import Link, Readiness, SoftwareChanged, wait_until_ready
from .reads import OK, BlockReader, EcuSilent, MemoryTarget, SpanRead

#: An island: a sampled extent of an erased area whose samples held data, read whole.
ISLAND = "island"


class ImageStopped(Exception):
    """The job cannot go on: it is saved as it stands and the reason reported."""


class Saved(Exception):
    """The job paused and did not resume in the run: it is saved for ``--resume``."""


@dataclass(frozen=True)
class Pacing:
    """How often a run checks the ECU, and how long a pause waits."""

    health_every_s: float = 30.0
    wait_s: float = 15 * 60.0
    poll_s: float = 30.0


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobReader:
    """One run's reading state over a job, and the read, re-read and pause steps."""

    def __init__(
        self,
        link: Link,
        target: MemoryTarget,
        preferred: int,
        pacing: Pacing,
        *,
        throttle_ms: int = 0,
        skip: Callable[[], bool],
        notice: Callable[[str], None],
        sleep: Callable[[float], Awaitable[None]],
        clock: Callable[[], float],
    ):
        assert target.format is not None
        self.link, self.target, self.pacing = link, target, pacing
        self.skip, self.notice, self.sleep, self.clock = skip, notice, sleep, clock
        self.rate: Rate = start_rate(preferred, throttle_ms=throttle_ms)
        self.reader = BlockReader(
            link.terminal, target, target.format, self.rate.block, throttle_s=throttle_ms / 1000
        )
        self.reader.on_event = self._on_event
        self.line = ProgressLine()
        self.writer: DumpWriter | None = None
        self.plan: JobPlan | None = None
        self.readiness: Readiness | None = None
        self.verdicts: Counter[str] = Counter()
        self._checked = clock()

    def resume_rate(self, block: int | None) -> None:
        """Start at the block a job had reached, never above it."""
        self.rate = start_rate(self.rate.preferred, block=block, throttle_ms=self.rate.throttle_ms)
        self.reader.block = self.rate.block

    def _on_event(self, event: str) -> None:
        self.rate = advance(self.rate, event)
        if event == OK:
            self.reader.block = self.rate.block
        else:
            self.reader.block = min(self.reader.block, self.rate.block)
        self.reader.throttle_s = self.rate.throttle_ms / 1000

    def rate_doc(self) -> dict[str, int]:
        return {"block": self.rate.block, "throttle_ms": self.rate.throttle_ms}

    def _job(self) -> tuple[DumpWriter, JobPlan]:
        assert self.writer is not None and self.plan is not None
        return self.writer, self.plan

    def _check_skip(self) -> None:
        if self.skip():
            raise Saved("stopped by you")

    # -- reads ------------------------------------------------------------------------

    async def read_extent(self, extent: PlannedExtent) -> None:
        """Read one planned extent and store it; a sample that holds data reads it whole."""
        writer, _plan = self._job()
        self.reader.block = self.rate.block
        span = await self._read_kind(extent)
        kind = extent.kind
        if kind == SAMPLE and any(not is_erased(p.data) for p in span.pieces):
            span = await self.reader.read_span(extent.start, extent.end, self.skip)
            span.events.append(ISLAND)
            kind = FULL
        if span.skipped_at is not None:
            raise Saved("stopped by you")
        if span.refused_rest == "unsupported":
            raise ImageStopped("the ECU no longer supports ReadMemoryByAddress (NRC 0x11)")
        writer.dump.rate = self.rate_doc()
        writer.add(span.pieces, span.gaps, extent_record(extent.start, extent.end, span, kind))

    async def _read_kind(self, extent: PlannedExtent) -> SpanRead:
        _writer, plan = self._job()
        if extent.kind == FULL:
            return await self.reader.read_span(extent.start, extent.end, self.skip)
        whole = SpanRead()
        for lo, hi in plan.spans(extent):
            self._check_skip()
            part = await self.reader.read_span(lo, hi)
            whole.pieces += part.pieces
            whole.events += part.events
            whole.reads += part.reads
            for gap in part.gaps:
                whole.add_gap(gap.start, gap.end, gap.result, gap.nrc)
        return whole

    async def reread(self, extent: Extent) -> tuple[list[Stretch], str]:
        """Read again what ``extent`` read, stretch by stretch; the pieces and their hash."""
        writer, _plan = self._job()
        pieces: list[Stretch] = []
        for s in writer.dump.read_span(extent.start, extent.end):
            pieces += (await self.reader.read_span(s.start, s.end)).pieces
        return pieces, pieces_hash(pieces)

    async def check_twice(self, extent: Extent) -> str:
        """Read ``extent`` again and settle any disagreement with a third read."""
        writer, _plan = self._job()
        _pieces, second = await self.reread(extent)
        if second == extent.sha256:
            return SAME
        third_pieces, third = await self.reread(extent)
        verdict = settle(extent.sha256, second, third)
        if verdict == FIRST_READ_ERROR:
            writer.replace(replace(extent, sha256=third, verified=verdict), third_pieces)
        return verdict

    # -- pauses -----------------------------------------------------------------------

    async def guarded[T](self, step: Callable[[], Awaitable[T]]) -> T:
        """Run one step; on silence, a failed check or a drop, pause and run it again."""
        while True:
            try:
                self._check_skip()
                if self.clock() - self._checked >= self.pacing.health_every_s:
                    self._checked = self.clock()
                    assert self.readiness is not None
                    if (why := await self.readiness.problem(self.link.terminal)) is not None:
                        await self.pause(why)
                        continue
                return await step()
            except EcuSilent as e:
                await self.pause(str(e))
            except SoftwareChanged as e:
                raise ImageStopped(str(e)) from None
            except transport_error_types() as e:
                self.link.dropped = True
                await self.pause(f"the link dropped ({e})")

    async def pause(self, reason: str) -> None:
        """Wait for every check to pass again, then prove continuity; or save the job."""
        writer, _plan = self._job()
        assert self.readiness is not None
        self.line.clear()
        record: dict[str, Any] = {"at": now(), "reason": reason}
        writer.dump.pauses.append(record)
        writer.save()
        self.notice(f"paused: {reason}; waiting up to {self.pacing.wait_s / 60:.0f} min")
        ready = await wait_until_ready(
            self.link,
            self.readiness,
            wait_s=self.pacing.wait_s,
            poll_s=self.pacing.poll_s,
            notice=self.notice,
            stop=self.skip,
            sleep=self.sleep,
            clock=self.clock,
        )
        if not ready:
            record["how"] = "saved for --resume"
            writer.save()
            raise Saved(reason)
        self.reader.terminal = self.link.terminal
        await self.continuity()
        record.update(resumed=now(), how="resumed in the run")
        writer.save()
        self.notice("resumed")

    async def continuity(self) -> None:
        """Re-read the job's first extent and its last two; any change stops the job."""
        writer, _plan = self._job()
        for extent in continuity_extents(writer.dump):
            if await self.check_twice(extent) not in (SAME, REREAD_ERROR):
                raise ImageStopped(
                    f"0x{extent.start:X}-0x{extent.end:X} no longer reads as the job read it: "
                    "the ECU's memory is not what this job holds"
                )

    # -- the passes -------------------------------------------------------------------

    def missing(self) -> list[PlannedExtent]:
        writer, plan = self._job()
        return missing(plan, writer.dump)

    async def read_all(self) -> None:
        """Read every extent the job has not read yet."""
        writer, _plan = self._job()
        todo = self.missing()
        began, before = self.clock(), writer.dump.bytes_read
        for i, extent in enumerate(todo, 1):
            speed = (writer.dump.bytes_read - before) / max(1e-6, self.clock() - began)
            self.line.update(
                f"    reading 0x{extent.start:X}  [{i}/{len(todo)}]  block {self.rate.block}"
                f"  {speed:.0f} B/s"
            )
            await self.guarded(lambda e=extent: self.read_extent(e))

    async def verify(self) -> None:
        """Read the extents the plan checks a second time, and record each verdict."""
        writer, plan = self._job()
        starts = set(recheck_starts(plan, writer.dump.extents))
        todo = [e for e in writer.dump.extents if e.start in starts and e.verified is None]
        for i, extent in enumerate(todo, 1):
            self.line.update(f"    checking 0x{extent.start:X}  [{i}/{len(todo)}]")
            verdict = await self.guarded(lambda e=extent: self.check_twice(e))
            self.verdicts[verdict] += 1
            if verdict == VOLATILE:
                writer.update(replace(extent, verified=verdict))
                raise ImageStopped(
                    f"0x{extent.start:X}-0x{extent.end:X} read three different ways: RAM, a "
                    "mapped peripheral or a link fault"
                )
            if verdict != FIRST_READ_ERROR:
                writer.update(replace(extent, verified=verdict))
