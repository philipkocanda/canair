"""A memory image job: the plan a dump is read to, and where a resume starts.

``canair memory image`` writes its plan into the dump's manifest (``plan:``) before the
first read, then reads it extent by extent. Everything a resume needs is in the manifest:
the plan says which extents there are, the ``extents:`` records say which are done and what
they hashed to, and ``rate:`` says the block the run had reached.

An extent is :data:`EXTENT_SIZE` bytes, aligned to a multiple of it. A ``full`` extent is
read whole. A ``sample`` extent, in an erased area of a sparse job, is read
:data:`SAMPLE_SIZE` bytes every :data:`SAMPLE_EVERY`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from .store import Dump, Extent

SPARSE: Final = "sparse"
THOROUGH: Final = "thorough"
MODES: Final = (SPARSE, THOROUGH)

FULL: Final = "full"
SAMPLE: Final = "sample"
DATA: Final = "data"
ERASED: Final = "erased"

EXTENT_SIZE: Final = 0x1000
SAMPLE_EVERY: Final = 0x400
SAMPLE_SIZE: Final = 0x40


@dataclass(frozen=True)
class Area:
    """A run of addresses the survey found holding data, or erased."""

    start: int
    end: int
    kind: str

    @property
    def size(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class PlannedExtent:
    """One extent of the plan and how it is read."""

    start: int
    end: int
    kind: str


def spans(
    extent: PlannedExtent | Extent, every: int = SAMPLE_EVERY, size: int = SAMPLE_SIZE
) -> list[tuple[int, int]]:
    """The address spans an extent of its kind is read as.

    Samples alternate between ``size`` and ``size - 1`` bytes, so two in a row never ask for
    the same length (the stale-reply check), and a resumed job samples the same bytes.
    """
    if extent.kind != SAMPLE:
        return [(extent.start, extent.end)]
    short = 1 if size > 1 else 0
    return [
        (a, min(a + size - 1 - short * (i % 2), extent.end))
        for i, a in enumerate(range(extent.start, extent.end + 1, every))
    ]


def _hex(n: int) -> str:
    return f"0x{n:X}"


def _int(value: Any) -> int:
    return value if isinstance(value, int) else int(str(value), 0)


@dataclass(frozen=True)
class JobPlan:
    """What a job reads, how, and the conditions it reads under."""

    mode: str
    start: int
    end: int
    areas: tuple[Area, ...]
    ceiling: int
    software: str | None = None
    state: str | None = None
    health: str | None = None
    alias: int | None = None
    flash_size: int | None = None
    baseline: dict[str, list[str] | None] = field(default_factory=dict)
    seed: int = 0
    extent_size: int = EXTENT_SIZE
    sample_every: int = SAMPLE_EVERY
    sample_size: int = SAMPLE_SIZE

    def extents(self) -> list[PlannedExtent]:
        """Every extent of the plan, in address order: the areas, cut at extent boundaries.

        An extent is read whole when the job is thorough or any of it holds data.
        """
        covered: list[list[int]] = []
        for a in sorted(self.areas, key=lambda a: a.start):
            if covered and covered[-1][1] + 1 >= a.start:
                covered[-1][1] = max(covered[-1][1], a.end)
            else:
                covered.append([a.start, a.end])
        out: list[PlannedExtent] = []
        size = self.extent_size
        for start, end in covered:
            lo = start
            while lo <= end:
                hi = min(end, (lo // size + 1) * size - 1)
                full = self.mode == THOROUGH or any(
                    a.kind == DATA and a.start <= hi and a.end >= lo for a in self.areas
                )
                out.append(PlannedExtent(lo, hi, FULL if full else SAMPLE))
                lo = hi + 1
        return out

    def spans(self, extent: PlannedExtent | Extent) -> list[tuple[int, int]]:
        return spans(extent, self.sample_every, self.sample_size)

    def to_doc(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "mode": self.mode,
            "space": [_hex(self.start), _hex(self.end)],
            "areas": [
                {"start": _hex(a.start), "end": _hex(a.end), "kind": a.kind} for a in self.areas
            ],
            "ceiling": self.ceiling,
            "software": self.software,
            "state": self.state,
            "health": self.health,
            "alias": None if self.alias is None else _hex(self.alias),
            "flash_size": None if self.flash_size is None else _hex(self.flash_size),
            "baseline": self.baseline or None,
            "seed": self.seed,
            "extent_size": _hex(self.extent_size),
            "sample": {"every": _hex(self.sample_every), "size": _hex(self.sample_size)},
        }
        return {k: v for k, v in doc.items() if v is not None}

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> JobPlan:
        start, end = (_int(x) for x in doc["space"])
        sample = doc.get("sample") or {}
        return cls(
            mode=str(doc["mode"]),
            start=start,
            end=end,
            areas=tuple(
                Area(_int(a["start"]), _int(a["end"]), str(a["kind"]))
                for a in doc.get("areas") or []
            ),
            ceiling=int(doc["ceiling"]),
            software=doc.get("software"),
            state=doc.get("state"),
            health=doc.get("health"),
            alias=None if doc.get("alias") is None else _int(doc["alias"]),
            flash_size=None if doc.get("flash_size") is None else _int(doc["flash_size"]),
            baseline={
                str(k): (None if v is None else list(v))
                for k, v in (doc.get("baseline") or {}).items()
            },
            seed=int(doc.get("seed") or 0),
            extent_size=_int(doc.get("extent_size", EXTENT_SIZE)),
            sample_every=_int(sample.get("every", SAMPLE_EVERY)),
            sample_size=_int(sample.get("size", SAMPLE_SIZE)),
        )


def job_plan(dump: Dump) -> JobPlan | None:
    """The plan a dump was read to, or ``None`` for a plain ``memory dump``."""
    return JobPlan.from_doc(dump.plan) if dump.plan else None


def missing_extents(plan: JobPlan, dump: Dump) -> list[PlannedExtent]:
    """The planned extents the dump has no record of yet, in address order."""
    done = {e.start for e in dump.extents}
    return [e for e in plan.extents() if e.start not in done]


def continuity_extents(dump: Dump) -> list[Extent]:
    """The extents a resume re-reads first: the job's first and its last two finished."""
    done = sorted(dump.extents, key=lambda e: e.start)
    if not done:
        return []
    picked = {done[0].start: done[0], **{e.start: e for e in done[-2:]}}
    return [picked[k] for k in sorted(picked)]
