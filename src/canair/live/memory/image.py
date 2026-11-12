"""``canair memory image``: read an ECU's whole memory image unattended, resumably and checked.

Orchestration only: the reads are :class:`~canair.live.memory.reads.BlockReader`'s, the
planning reads :mod:`.survey`'s, the extent passes :mod:`.extents`'s, the checks
:mod:`.liveness`'s, and every decision comes from the pure modules
(:mod:`canair.memory.plan`, :mod:`~canair.memory.rate`, :mod:`~canair.memory.verify`,
:mod:`~canair.memory.job`)::

    preflight ─► map ─► size & aliases ─► survey ─► plan (show, confirm) ─► read ─► verify
                                                         ▲                    │
                                                         └──── pause/resume ◄─┘

The job is a dump on disk from the moment the plan is confirmed, and grows extent by
extent. A silent ECU, a failed check or a dropped link pauses it: the run polls gently for
a bounded time and reads on by itself, or saves the job for ``--resume``. A resume (in the
run or later) proves it talks to the same ECU and software, and re-reads the job's first
extent and its last two, before it reads on.

Only ReadMemoryByAddress (``0x23``) touches memory. The other requests are reads too: the
software version, the health PID, the state's signals and the fault codes.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from canair.ecus.can_buses import bus_neighbours
from canair.ecus.memory import MemoryRegion
from canair.live.dtc import read_ecu_dtcs
from canair.live.identity.software import read_software_version
from canair.live.monitor.reconnect import Reconnector
from canair.memory.job import SAMPLE_SIZE, SPARSE, JobPlan, job_plan
from canair.memory.plan import DEFAULT_STRIDE, Estimate, answers_everywhere, estimate
from canair.memory.rate import Rate, ceiling, preferred_block
from canair.memory.store import Dump, DumpWriter
from canair.memory.verify import DtcChange, dtc_changes, find_strings, identity_strings
from canair.profile.model import Profile
from canair.states.predicates import StateRule
from canair.transport.errors import transport_error_types
from canair.transport.protocol import Terminal
from canair.uds.memory import default_scan_space
from canair.yaml_edit.memory import record_scan

from .dump import block_for
from .extents import ImageStopped, JobReader, Pacing, Saved, now
from .liveness import (
    POLL_S,
    Conditions,
    Link,
    Readiness,
    SoftwareChanged,
    health_read,
    state_reads,
    unmet,
)
from .reads import EcuSilent, MemoryTarget, maybe_session
from .scan import ScanStopped, space_and_step
from .survey import MapFailed, Survey, find_block, map_ranges, recorded_regions, survey


@dataclass(frozen=True)
class ImageOptions:
    """How the run plans and reads (the flags of ``canair memory image``)."""

    mode: str = SPARSE
    ranges: tuple[tuple[int, int], ...] = ()
    step: int | None = None
    stride: int = DEFAULT_STRIDE
    block_limit: int | None = None
    state: str | None = None
    health_pid: str | None = None
    health_every_s: float = 30.0
    wait_s: float = 15 * 60.0
    poll_s: float = POLL_S
    session_mode: str | None = None
    throttle_ms: int = 0
    label: str | None = None
    digit: bool = False
    plan_only: bool = False
    dry_run: bool = False


@dataclass
class ImageRun:
    """How a run went: the plan, the dump and every check's result."""

    ecu: str
    dump: Dump | None = None
    plan: JobPlan | None = None
    survey: Survey | None = None
    estimate: Estimate | None = None
    stopped: str | None = None
    saved: str | None = None
    declined: bool = False
    resumed: bool = False
    software: str | None = None
    reads: int = 0
    seconds: float = 0.0
    bytes_now: int = 0
    rate: Rate | None = None
    verdicts: Counter[str] = field(default_factory=Counter)
    dtc_changes: list[DtcChange] = field(default_factory=list)
    identity: dict[str, tuple[str, list[int]]] = field(default_factory=dict)
    written: Path | None = None

    @property
    def complete(self) -> bool:
        return self.dump is not None and self.dump.complete


ConfirmPlan = Callable[[ImageRun], bool]


@dataclass
class _Context:
    """What the steps of one run share."""

    job: JobReader
    target: MemoryTarget
    options: ImageOptions
    store: Path
    pids_data: Mapping[str, Any]
    profile: Profile
    rule: StateRule | None
    make: str | None
    verified: bool = False
    start_bytes: int = 0
    written: Path | None = None


def _conditions(ctx: _Context, health: str | None) -> Conditions:
    try:
        read = health_read(ctx.target.ecu_def, ctx.target.name, health)
        reads = tuple(state_reads(ctx.pids_data, ctx.rule)) if ctx.rule else ()
    except ValueError as e:
        raise ImageStopped(str(e)) from None
    return Conditions(read, ctx.rule, reads)


async def _software(ctx: _Context) -> str | None:
    return await read_software_version(
        ctx.job.link.terminal, ctx.target.tx_id, ctx.target.protocol, make=ctx.make
    )


async def _fault_codes(ctx: _Context, ecus: list[str]) -> dict[str, list[str] | None]:
    """Each ECU's stored fault codes, or ``None`` for one that did not answer."""
    defs = ctx.pids_data.get("ecus") or {}
    out: dict[str, list[str] | None] = {}
    for name in ecus:
        tx = (defs.get(name) or {}).get("tx_id")
        if tx is None:
            continue
        ctx.job.line.update(f"    reading the fault codes of {name}…")
        result = await read_ecu_dtcs(
            ctx.job.link.terminal, int(tx), 0xFF, "auto", False, profile=ctx.profile
        )
        codes = result.get("dtcs") if "dtcs" in result else None
        out[name] = None if codes is None else sorted(str(d["dtc"]) for d in codes)
    return out


async def _map(ctx: _Context, software: str | None) -> tuple[list[tuple[int, int]], str]:
    """The readable regions to survey: recorded for this build, or mapped and recorded now.

    A fresh map covers ``--range`` or the format's default space, and is written to the
    ECU's ``memory:`` section (not with ``--dry-run``) together with the block the search
    finds when none is recorded.
    """
    job, opts, fmt = ctx.job, ctx.options, ctx.target.format
    assert fmt is not None
    regions = [] if opts.ranges else recorded_regions(ctx.target, software)
    source = "the readable regions in ecus/"
    fresh: list[MemoryRegion] = []
    if not regions:
        try:
            ranges, step = space_and_step(fmt, list(opts.ranges) or None, opts.step)
        except ScanStopped as e:
            raise ImageStopped(f"{ctx.target.name} records no readable region: {e}") from None
        mapped = await map_ranges(job.reader, fmt, ranges, step, software, job.line.update)
        regions, fresh = mapped.readable, mapped.regions
        source = "a new map of " + ", ".join(f"0x{a:X}-0x{b:X}" for a, b in ranges)
    if not regions:
        raise ImageStopped("no address in the range answers a memory read")
    _refuse_an_unchecked_space(ctx, regions)
    found_block = None
    if ctx.target.memory is None or ctx.target.memory.max_block is None:
        job.line.update("    finding the largest block…")
        biggest = max(regions, key=lambda r: r[1] - r[0])
        # The run's own ceiling (--block-limit, else 64), never the scan's wider default.
        limit = block_for(ctx.target, opts.block_limit)
        found_block = await find_block(job.reader, fmt, biggest, limit)
        job.reader.known_block = found_block
        job.set_preferred(preferred_block(found_block, digit=opts.digit))
    if (fresh or found_block is not None) and not opts.dry_run:
        job.line.clear()
        ctx.written = record_scan(
            ctx.target.tx_id, fresh, fmt=fmt, max_block=found_block, profile=ctx.profile
        )
    return regions, source


def _refuse_an_unchecked_space(ctx: _Context, regions: list[tuple[int, int]]) -> None:
    """Stop before any larger read when the ECU answered its whole default space.

    Such an ECU does not check the range, so the size test, the alias test and the survey
    would read where the chip may have no memory. ``--range`` is the user saying where it is.
    """
    fmt = ctx.target.format
    assert fmt is not None
    space = None if ctx.options.ranges else default_scan_space(fmt)
    if space is None or not answers_everywhere(regions, space):
        return
    raise ImageStopped(
        f"every address of 0x{space[0]:X}-0x{space[1]:X} answers a memory read, so "
        f"{ctx.target.name} does not check the range and the map does not show where its "
        "memory is. A read where the chip has none can stop the ECU. Pass --range with the "
        "ranges its memory occupies (the chip's memory map, or what its boot code names)"
    )


async def _survey(ctx: _Context, software: str | None) -> Survey:
    """Map (or take the recorded map), then size, aliases and the survey."""
    job = ctx.job
    regions, source = await _map(ctx, software)
    size = min(job.rate.block, SAMPLE_SIZE)
    return await survey(
        job.reader, regions, source, stride=ctx.options.stride, size=size, progress=job.line.update
    )


def _plan(ctx: _Context, software: str | None, found: Survey, conditions: Conditions):
    preferred = ctx.job.rate.preferred
    return JobPlan(
        mode=ctx.options.mode,
        start=min(a for a, _ in found.regions),
        end=max(b for _, b in found.regions),
        areas=tuple(found.areas),
        ceiling=preferred,
        software=software,
        state=ctx.rule.name if ctx.rule else None,
        health=conditions.health.pid if conditions.health else None,
        alias=found.alias,
        flash_size=found.flash_size,
        seed=random.randrange(1 << 31),
        sample_size=min(preferred, SAMPLE_SIZE),
    )


def _new_job(ctx: _Context, plan: JobPlan, provenance: Mapping[str, Any]) -> Dump:
    fmt = ctx.target.format
    assert fmt is not None
    dump = Dump(
        ecu=ctx.target.name,
        tx_id=ctx.target.tx_id,
        memory_format=fmt.name,
        taken=now(),
        asked=[(plan.start, plan.end)],
        software=plan.software,
        label=ctx.options.label,
        profile=provenance.get("profile"),
        variant=provenance.get("variant"),
        revisions=dict(provenance.get("revisions") or {}),
        canair=provenance.get("canair"),
        transport=provenance.get("transport"),
        complete=False,
        plan=plan.to_doc(),
        rate=ctx.job.rate_doc(),
    )
    ctx.job.writer = DumpWriter.create(dump, ctx.store, address_width=2 * fmt.address_bytes)
    return dump


async def mode_memory_image(
    terminal: Terminal,
    target: MemoryTarget,
    store: Path,
    options: ImageOptions,
    *,
    pids_data: Mapping[str, Any],
    profile: Profile,
    rule: StateRule | None = None,
    make: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    confirm_plan: ConfirmPlan = lambda _run: True,
    resume: Dump | None = None,
    reconnect: Reconnector | None = None,
    skip: Callable[[], bool] = lambda: False,
    notice: Callable[[str], None] = lambda _m: None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ImageRun:
    """Plan and read (or resume) ``target``'s memory image into a job under ``store``.

    Raises :class:`ValueError` before any read for a target with no memory format, a dump
    that is not a job, or a block limit above the ECU's ``max_block``.
    """
    if target.format is None:
        raise ValueError(
            f"{target.name} has no memory format: run `canair scan memory` first, or pass --format"
        )
    plan = job_plan(resume) if resume is not None else None
    if resume is not None and plan is None:
        raise ValueError(f"{resume.id} is a plain dump, not a job: only a job can resume")
    if plan is not None:
        options = replace(options, mode=plan.mode, block_limit=plan.ceiling)
    top = ceiling(None, block_for(target, options.block_limit), target.format.max_size)
    pacing = Pacing(options.health_every_s, options.wait_s, options.poll_s)
    job = JobReader(
        Link(terminal, reconnect),
        target,
        preferred_block(top, digit=options.digit),
        pacing,
        throttle_ms=options.throttle_ms,
        skip=skip,
        notice=notice,
        sleep=sleep,
        clock=clock,
    )
    ctx = _Context(job, target, options, store, pids_data, profile, rule, make)
    run = ImageRun(target.name, plan=plan, resumed=resume is not None)
    began = clock()
    try:
        async with maybe_session(terminal, target.tx_id, options.session_mode):
            await _run(ctx, run, resume, provenance or {}, confirm_plan)
    except (ImageStopped, MapFailed, SoftwareChanged) as e:
        run.stopped = str(e)
    except (Saved, EcuSilent, *transport_error_types()) as e:
        # A pause that did not resume, or a check before the job could read on.
        run.saved = str(e)
    finally:
        job.line.clear()
        run.reads, run.seconds, run.rate = job.reader.reads, clock() - began, job.rate
        run.verdicts, run.written = job.verdicts, ctx.written
        if job.writer is not None:
            run.bytes_now = job.writer.dump.bytes_read - ctx.start_bytes
            done = ctx.verified and not job.missing()
            job.writer.finish(run.stopped is None and run.saved is None and done)
            run.dump = job.writer.dump
    return run


async def _run(
    ctx: _Context,
    run: ImageRun,
    resume: Dump | None,
    provenance: Mapping[str, Any],
    confirm_plan: ConfirmPlan,
) -> None:
    job, plan = ctx.job, run.plan
    conditions = _conditions(ctx, plan.health if plan else ctx.options.health_pid)
    run.software = await _software(ctx)
    if (why := await unmet(job.link.terminal, conditions)) is not None:
        raise ImageStopped(f"{why}: the run reads only while it holds")
    if resume is not None and plan is not None:
        if plan.software and run.software != plan.software:
            raise ImageStopped(str(SoftwareChanged(plan.software, run.software or "none")))
        job.writer = DumpWriter(resume)
        run.dump, ctx.start_bytes = resume, resume.bytes_read
        job.resume_rate((resume.rate or {}).get("block"))
    else:
        found = run.survey = await _survey(ctx, run.software)
        plan = run.plan = _plan(ctx, run.software, found, conditions)
        run.estimate = estimate(plan, job.rate.block, found.seconds_per_read)
        job.line.clear()
        if ctx.options.plan_only or not confirm_plan(run):
            run.declined = not ctx.options.plan_only
            return
        neighbours = bus_neighbours(ctx.pids_data.get("ecus") or {}, ctx.target.name)
        plan = run.plan = replace(
            plan, baseline=await _fault_codes(ctx, [ctx.target.name, *neighbours])
        )
        run.dump = _new_job(ctx, plan, provenance)
    job.plan = plan
    job.readiness = Readiness(ctx.target, conditions, run.software, plan.start, ctx.make)
    if resume is not None:
        await job.continuity()
    await job.read_all()
    await job.verify()
    ctx.verified = True
    await _finish(ctx, run, plan)


async def _finish(ctx: _Context, run: ImageRun, plan: JobPlan) -> None:
    """The end checks: the same software, the fault codes against the baseline, identity."""
    assert ctx.job.writer is not None
    software = await _software(ctx)
    if plan.software and software != plan.software:
        ctx.verified = False
        raise ImageStopped(str(SoftwareChanged(plan.software, software or "none")))
    after = await _fault_codes(ctx, list(plan.baseline))
    run.dtc_changes = dtc_changes(plan.baseline, after)
    texts = identity_strings(ctx.target.ecu_def.get("identity"))
    hits = find_strings(ctx.job.writer.dump, texts.values())
    run.identity = {f: (t, hits.get(t, [])) for f, t in texts.items()}
