"""Hex identifier ranges: ``B400-B41F,B100-B11F`` and the ids they cover.

A leaf module (no I/O, no argparse) shared by the scan planner, the scan mode and the scan
capture builder, so a list of ranges is parsed, counted and rendered one way everywhere.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import cast

IdRange = tuple[int, int]

_RANGE = re.compile(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$")


def parse_id_ranges(text: str) -> tuple[IdRange, ...]:
    """Parse ``START-END[,START-END...]`` (hex) into ranges, in the order given.

    Raises :class:`ValueError` on an empty list, a malformed item or a range that runs
    backwards.
    """
    items = [item.strip() for item in str(text).split(",")]
    if not any(items):
        raise ValueError("empty range")
    ranges: list[IdRange] = []
    for item in items:
        match = _RANGE.match(item)
        if not match:
            raise ValueError(f"invalid range {item!r}: expected START-END in hex, e.g. 01-FF")
        start, end = int(match.group(1), 16), int(match.group(2), 16)
        if start > end:
            raise ValueError(f"invalid range {item!r}: start is after end")
        ranges.append((start, end))
    return tuple(ranges)


def as_id_ranges(value: IdRange | Sequence[IdRange]) -> tuple[IdRange, ...]:
    """Normalize one ``(start, end)`` pair or a sequence of them to a tuple of ranges."""
    if isinstance(value[0], int):
        pair = cast(IdRange, value)
        return ((pair[0], pair[1]),)
    return tuple((start, end) for start, end in cast(Sequence[IdRange], value))


def iter_ids(ranges: Sequence[IdRange]) -> Iterator[int]:
    """Every id the ranges cover, in order, each once even where ranges overlap."""
    seen: set[int] = set()
    for start, end in ranges:
        for value in range(start, end + 1):
            if value not in seen:
                seen.add(value)
                yield value


def count_ids(ranges: Sequence[IdRange]) -> int:
    """How many distinct ids the ranges cover."""
    return sum(1 for _ in iter_ids(ranges))


def format_id_ranges(ranges: Sequence[IdRange], width: int) -> str:
    """Render ranges as ``START-END,...`` with ``width`` hex digits per id."""
    return ",".join(f"{start:0{width}X}-{end:0{width}X}" for start, end in ranges)
