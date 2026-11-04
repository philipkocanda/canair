"""Field maps: one support-map bit per *parameter* of a record, whatever its width.

Hyundai/Kia record identifiers number their parameters from 1 and lead with a "supported
parameters" bitmap: map bit 7 of the first byte is parameter 1, bit 0 of the fourth byte
parameter 32 (MSB first). A record with more than 32 parameters carries another bitmap as
parameter 32, 64, …, each covering the next 31. The per-bit and per-byte layouts of
:mod:`canair.analysis.support_map` are the special cases where every parameter is one bit or one
byte; this module handles mixed widths (the service-21 pages). See
``plans/2026-11-03-support-map-field-maps.md``.

The map gives the *count* of parameters, not their widths, so the widths come from the defined
signals (:func:`known_fields`) and the rest is search: :func:`solve_fields` enumerates every way
to split the covered bytes into parameters such that

* a defined field is never cut or shared with another,
* an unmarked parameter's bytes never change (an unsupported parameter holds fill),
* every marked parameter exists (a set bit past the end of the record is a contradiction),
* parameter 32 of a block is either the record's last parameter or the next 4-byte bitmap.

One split is ``exact``; several are ``consistent`` (the bytes no defined signal reads could be
grouped more than one way); none is ``broken``. There is no probability score: the count is the
test.

A leaf like :mod:`canair.analysis.support_map`: rows of data bytes (SID and DID echo stripped)
and byte sets in, results out. No WiCAN indices, no captures.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from functools import cache
from typing import Literal

FieldVerdict = Literal["exact", "consistent", "broken"]
Order = Literal["msb", "lsb"]

# Widest parameter an undefined run of bytes may form (a 32-bit counter); a defined signal
# may be wider (an ASCII tag) and is taken as it is.
MAX_FIELD_BYTES = 4
# The search recurses once per parameter; records this long carry no bitmap seen so far.
MAX_RECORD_BYTES = 512
# Stop counting splits here: two already means "not unique", the rest is cost.
_SOLUTION_CAP = 2


def known_fields(byte_sets: Iterable[Iterable[int]]) -> list[tuple[int, int]]:
    """Field extents ``(start, end)`` (end exclusive) implied by the defined signals' bytes.

    A signal reading non-contiguous bytes, or a contiguous run its narrower neighbours already
    cover, combines several parameters (``BATTERY_POWER`` = current x voltage) and is dropped.
    The rest are merged where they overlap: several flags of one byte, or a coarse and a fine
    reading of one value, are one parameter.
    """
    sets = {frozenset(s) for s in byte_sets if s}
    runs = [s for s in sets if max(s) - min(s) + 1 == len(s)]
    kept = []
    for s in runs:
        inner = [t for t in runs if t < s]
        if inner and frozenset().union(*inner) == s:
            continue
        kept.append((min(s), max(s) + 1))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(kept):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def mask_bit(mask: bytes, index: int, order: Order = "msb") -> bool:
    """Whether ``mask`` marks parameter ``index + 1`` of its block."""
    if order == "msb":
        return bool((mask[index // 8] >> (7 - index % 8)) & 1)
    return bool((int.from_bytes(mask, "big") >> index) & 1)


@dataclass(frozen=True)
class Field:
    """One parameter of a split. Offsets are data offsets, ``end`` exclusive."""

    start: int
    end: int
    number: int  # parameter number within its block, from 1
    block: int  # which bitmap covers it (0 = the leading one)
    marked: bool
    defined: bool  # holds a defined signal
    is_map: bool = False  # the next block's bitmap


@dataclass(frozen=True)
class FieldFit:
    """The result of :func:`solve_fields` for one placement."""

    at: int
    length: int
    order: Order
    n_rows: int
    width: int  # data bytes in the rows
    blocks: tuple[tuple[int, bytes], ...]  # (data offset, mask) of every bitmap on all splits
    verdict: FieldVerdict
    splits: int  # number of splits found, capped at 2
    fields: tuple[Field, ...]  # the split, when it is unique
    field_counts: tuple[int, int]  # fewest and most data parameters over all splits
    fill: frozenset[int]  # data offsets in an unmarked parameter on every split
    claimed: int  # covered bytes a defined signal reads
    reason: str = ""  # why it is broken

    @property
    def mask(self) -> bytes:
        return self.blocks[0][1]

    @property
    def marked(self) -> int:
        """Set bits of the leading bitmap."""
        return sum(bin(b).count("1") for b in self.mask)

    @property
    def covered(self) -> int:
        return self.width - self.at - self.length


def solve_fields(
    rows: Sequence[bytes],
    known: Sequence[tuple[int, int]],
    *,
    at: int = 0,
    length: int = 4,
    order: Order = "msb",
) -> FieldFit | None:
    """Split the bytes after a ``length``-byte bitmap at ``at`` into parameters; see module doc.

    ``rows`` must already agree on the bitmap (the caller picks the modal one); ``known`` are
    :func:`known_fields` extents. None when the rows are too short to hold the bitmap, the
    bitmap changes between them, or the record is longer than :data:`MAX_RECORD_BYTES`.
    """
    if not rows:
        return None
    width = min(len(r) for r in rows)
    first = at + length
    if first >= width or width > MAX_RECORD_BYTES:
        return None
    const = [len({r[o] for r in rows}) == 1 for o in range(width)]
    if not all(const[at:first]):
        return None
    block_size = 8 * length
    owner: dict[int, tuple[int, int]] = {}
    for span in known:
        for o in range(max(span[0], first), min(span[1], width)):
            owner[o] = span

    def mask_at(start: int) -> bytes:
        return bytes(rows[0][start : start + length])

    def last_set(start: int) -> int:
        mask = mask_at(start)
        return max((k for k in range(block_size) if mask_bit(mask, k, order)), default=-1)

    def steps(i: int, f: int, ms: int) -> list[tuple[int, int, int, Field]]:
        """Every next parameter from state (byte ``i``, parameter index ``f``, bitmap at ``ms``)."""
        out: list[tuple[int, int, int, Field]] = []
        if f >= block_size or i >= width or (i in owner and owner[i][0] < i):
            return out  # block full, record over, or inside a defined field
        mask = mask_at(ms)
        marked = mask_bit(mask, f, order)
        block = 0  # numbered when the unique split is walked; steps() cannot know it
        if f == block_size - 1 and marked and i + length < width:
            span = range(i, i + length)
            if all(const[o] for o in span) and not any(o in owner for o in span):
                out.append(
                    (i + length, 0, i, Field(i, i + length, f + 1, block, True, False, True))
                )
        for j in range(i + 1, width + 1):
            spans = {owner[o] for o in range(i, j) if o in owner}
            if len(spans) > 1:
                break
            if spans:
                (span,) = spans
                if span[1] > j:
                    continue  # would cut the defined field
                if j - i > max(MAX_FIELD_BYTES, span[1] - span[0]):
                    break
            elif j - i > MAX_FIELD_BYTES:
                break
            if not marked and not all(const[o] for o in range(i, j)):
                continue
            if f == block_size - 1 and j != width:
                continue  # parameter 32 of a block ends the record unless it is a bitmap
            out.append((j, f + 1, ms, Field(i, j, f + 1, block, marked, bool(spans))))
        return out

    def done(i: int, f: int, ms: int) -> bool:
        return i == width and last_set(ms) < f

    @cache
    def tally(i: int, f: int, ms: int) -> tuple[int, int, int]:
        """(splits capped, fewest, most) data parameters from this state to the end."""
        if done(i, f, ms):
            return 1, 0, 0
        if i >= width or f >= block_size:
            return 0, 0, 0
        n, lo, hi = 0, 10**6, -1
        for j, g, nms, fld in steps(i, f, ms):
            sub, slo, shi = tally(j, g, nms)
            if sub:
                add = 0 if fld.is_map else 1
                n = min(_SOLUTION_CAP, n + sub)
                lo, hi = min(lo, slo + add), max(hi, shi + add)
        return (n, lo, hi) if n else (0, 0, 0)

    start = (first, 0, at)
    splits, lo, hi = tally(*start)
    claimed = sum(1 for o in range(first, width) if o in owner)
    blocks: list[tuple[int, bytes]] = [(at, mask_at(at))]
    if not splits:
        most = len({owner[o] for o in owner}) + sum(
            1 for o in range(first, width) if o not in owner
        )
        return FieldFit(
            at, length, order, len(rows), width, tuple(blocks), "broken", 0, (), (0, 0),
            frozenset(), claimed, _broken_reason(mask_at(at), order, most),
        )  # fmt: skip

    # Walk every edge that lies on some split: a byte is fill when each such edge covering it
    # is an unmarked parameter.
    marked_cover: dict[int, set[bool]] = {}
    map_starts: set[int] = set()
    seen: set[tuple[int, int, int]] = set()
    frontier = [start]
    block_of = {at: 0}
    while frontier:
        state = frontier.pop()
        if state in seen:
            continue
        seen.add(state)
        for j, g, nms, fld in steps(*state):
            if not tally(j, g, nms)[0]:
                continue
            if fld.is_map:
                map_starts.add(fld.start)
                block_of.setdefault(fld.start, len(block_of))
            else:
                for o in range(fld.start, fld.end):
                    marked_cover.setdefault(o, set()).add(fld.marked)
            frontier.append((j, g, nms))
    fill = frozenset(o for o, kinds in marked_cover.items() if kinds == {False})
    # Every later bitmap some split places, so a declaration can name them even when the split
    # is not unique (they are usually forced: 4 constant bytes exactly 31 parameters on).
    blocks += [(s, mask_at(s)) for s in sorted(map_starts)]

    fields: tuple[Field, ...] = ()
    if splits == 1:
        path: list[Field] = []
        state = start
        while not done(*state):
            j, g, nms, fld = next(s for s in steps(*state) if tally(s[0], s[1], s[2])[0])
            path.append(replace(fld, block=block_of[state[2]]))
            state = (j, g, nms)
        fields = tuple(path)
    verdict: FieldVerdict = "exact" if splits == 1 else "consistent"
    return FieldFit(
        at, length, order, len(rows), width, tuple(blocks), verdict, splits, fields, (lo, hi),
        fill, claimed,
    )  # fmt: skip


def _broken_reason(mask: bytes, order: Order, most: int) -> str:
    """Why no split exists; ``most`` is how many parameters the bytes could hold at best."""
    wanted = sum(1 for k in range(8 * len(mask)) if mask_bit(mask, k, order))
    if wanted > most:
        return f"the map marks {wanted} parameters but the bytes hold at most {most}"
    return "no split of the bytes keeps every unmarked parameter constant"
