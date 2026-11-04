"""The support-map marking rule: which data bits (or bytes) a map byte marks as signals.

Several Hyundai body and climate DIDs start their data with a few constant bytes that mark which
of the following bits or bytes are real signals. This is the one definition of how a map byte
marks them, shared by the declaration parser (:mod:`canair.uds.support_map`) and the detector
(:mod:`canair.analysis.support_map`).

Pure, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable

# A mark is the start of a field at most this many bits wide (the mark plus continuations).
# Wide enough for the IGPM's 2-bit turn-signal field, narrow enough that BCM 22B005's
# light-stalk bits (a gap of unmarked, never-set bits above the mark) stay violations.
MAX_FIELD_WIDTH = 3

# A map is at most this many bytes long.
MAX_MAP_LEN = 4


def reverse_bits(value: int) -> int:
    """``value`` with its 8 bits mirrored (map bit 7 becomes data bit 0)."""
    return int(f"{value & 0xFF:08b}"[::-1], 2)


def marked_bits(map_byte: int) -> frozenset[int]:
    """Data bit positions (0 = LSB) marked by one map byte, MSB first."""
    return frozenset(bit for bit in range(8) if (map_byte >> (7 - bit)) & 1)


def marked_byte_indices(mask: bytes, covered_len: int) -> frozenset[int]:
    """Covered-byte indices marked by a per-byte map, MSB first across the map."""
    return frozenset(
        i for i in range(min(covered_len, 8 * len(mask))) if (mask[i // 8] >> (7 - i % 8)) & 1
    )


def continuation_bits(marked: Iterable[int], active: Iterable[int]) -> frozenset[int]:
    """The ``active`` unmarked bits that extend a marked bit upward as one field.

    ``active`` are the unmarked bits that need explaining (ever set, or read by a signal). A
    bit extends a field when the bit below it is marked or already a continuation and the field
    would not exceed :data:`MAX_FIELD_WIDTH`.
    """
    marks = set(marked)
    wanted = set(active) - marks
    out: set[int] = set()
    start = -1
    for bit in range(8):
        if bit in marks:
            start = bit
        elif (
            bit in wanted and (bit - 1 in marks or bit - 1 in out) and bit - start < MAX_FIELD_WIDTH
        ):
            out.add(bit)
    return frozenset(out)
