"""The checks of a memory image: which extents to read twice, what disagreement means, and
what the finished image is checked against.

Pure. Stability is decided 2-of-3: an extent is read a second time; when the two reads
disagree, a third read settles which one the link spoiled (:func:`settle`). A third read
that matches neither is a volatile range — RAM, a mapped peripheral or a link fault — and
the job stops rather than guess which.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .job import JobPlan
from .store import Dump, Extent

#: The share of a sparse job's extents re-read at random.
RECHECK_FRACTION: Final = 0.05

SAME: Final = "same"
#: The second read was the link's error; the first stands.
REREAD_ERROR: Final = "reread_error"
#: The first read was the link's error; the second (and third) replace it.
FIRST_READ_ERROR: Final = "first_read_error"
VOLATILE: Final = "volatile"


def recheck_starts(plan: JobPlan, extents: Sequence[Extent]) -> list[int]:
    """The extents (by start) the verify step reads again.

    Thorough: all of them. Sparse: every extent that had an event while it was read, the
    first and last extent of each area, and :data:`RECHECK_FRACTION` of the rest, drawn
    with the plan's seed so a resumed job checks the same ones.
    """
    starts = sorted(e.start for e in extents)
    if plan.mode != "sparse":
        return starts
    chosen = {e.start for e in extents if e.events}
    for area in plan.areas:
        inside = [s for s in starts if area.start <= s <= area.end]
        if inside:
            chosen |= {inside[0], inside[-1]}
    rest = [s for s in starts if s not in chosen]
    k = round(len(rest) * RECHECK_FRACTION)
    chosen |= set(random.Random(plan.seed).sample(rest, k)) if k else set()
    return sorted(chosen)


def settle(first: str, second: str, third: str | None = None) -> str:
    """The verdict on an extent's hashes: :data:`SAME`, or the 2-of-3 decision."""
    if first == second:
        return SAME
    if third is None:
        raise ValueError("two reads disagree: a third settles it")
    if third == first:
        return REREAD_ERROR
    if third == second:
        return FIRST_READ_ERROR
    return VOLATILE


def find_strings(dump: Dump, strings: Iterable[str]) -> dict[str, list[int]]:
    """Every address in ``dump`` where each ASCII string starts."""
    out: dict[str, list[int]] = {}
    for text in dict.fromkeys(s for s in strings if s):
        needle = text.encode("ascii", "ignore")
        hits: list[int] = []
        for s in dump.stretches:
            at = s.data.find(needle)
            while at >= 0:
                hits.append(s.start + at)
                at = s.data.find(needle, at + 1)
        out[text] = hits
    return out


#: The identity fields of an ECU an image is searched for. Never the VIN.
IDENTITY_FIELDS: Final = ("part_number", "sw_id", "calibration", "ecu_id")


def identity_strings(identity: Mapping[str, object] | None) -> dict[str, str]:
    """``{field: text}`` of the identity fields the ECU declares."""
    return {
        f: str(identity[f]).strip()
        for f in IDENTITY_FIELDS
        if identity and identity.get(f) is not None and str(identity[f]).strip()
    }


@dataclass(frozen=True)
class DtcChange:
    """One ECU's fault codes before and after the run."""

    ecu: str
    new: tuple[str, ...]
    gone: tuple[str, ...]
    unknown: bool = False  # one of the two reads did not answer


def dtc_changes(
    before: Mapping[str, Sequence[str] | None], after: Mapping[str, Sequence[str] | None]
) -> list[DtcChange]:
    """What changed in each ECU's fault codes between the baseline and the end."""
    out: list[DtcChange] = []
    for ecu in dict.fromkeys([*before, *after]):
        a, b = before.get(ecu), after.get(ecu)
        if a is None or b is None:
            out.append(DtcChange(ecu, (), (), unknown=True))
            continue
        out.append(DtcChange(ecu, tuple(sorted(set(b) - set(a))), tuple(sorted(set(a) - set(b)))))
    return out
