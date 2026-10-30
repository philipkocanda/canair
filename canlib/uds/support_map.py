"""A PID's declared support map: what the profile says, resolved against a real payload.

A PID may carry ``support_map: {at: 0, length: 4, unit: bit, order: msb}`` (see
``plans/2026-10-29-support-bitmap-coverage.md``): ``length`` constant bytes at data offset ``at``
(counted after the SID and DID echo) mark which of the data that follows are real signals.
Only the *layout* is stored. The map bytes are read from each payload, so a declaration survives
an ECU software update that adds signals.

The marking rule itself lives in :mod:`canlib.analysis.support_map` (one definition, shared with
detection); this module reads the declaration, validates it, and answers per-position questions
for ``coverage`` and ``bix``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from canlib.analysis.support_map import (
    MAX_MAP_LEN,
    Unit,
    continuation_bits,
    marked_bits,
    marked_byte_indices,
)

UNITS: tuple[str, ...] = ("bit", "byte")
ORDERS: tuple[str, ...] = ("msb",)
DECL_FIELDS = ("at", "length", "unit", "order")


class SupportMapError(ValueError):
    """A ``support_map:`` declaration that is malformed."""


@dataclass(frozen=True)
class MapDecl:
    """The layout of a map, as declared on a PID."""

    at: int
    length: int
    unit: Unit
    order: str = "msb"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> MapDecl:
        """Parse and validate a ``support_map:`` mapping; raises :class:`SupportMapError`."""
        unknown = set(raw) - set(DECL_FIELDS)
        if unknown:
            raise SupportMapError(f"unknown support_map field(s): {', '.join(sorted(unknown))}")
        for name in ("at", "length", "unit"):
            if name not in raw:
                raise SupportMapError(f"support_map needs '{name}'")
        at, length, unit = raw["at"], raw["length"], raw["unit"]
        order = raw.get("order", "msb")
        for name, value in (("at", at), ("length", length)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise SupportMapError(f"support_map {name} must be an integer")
        if at < 0:
            raise SupportMapError("support_map at must be >= 0")
        if not 1 <= length <= MAX_MAP_LEN:
            raise SupportMapError(f"support_map length must be 1..{MAX_MAP_LEN}")
        if unit not in UNITS:
            raise SupportMapError(f"support_map unit must be one of {', '.join(UNITS)}")
        if order not in ORDERS:
            raise SupportMapError(f"support_map order must be one of {', '.join(ORDERS)}")
        return cls(at, length, unit, order)

    def flow(self) -> str:
        """The one-line flow-mapping form written into ``ecus/``."""
        return f"{{at: {self.at}, length: {self.length}, unit: {self.unit}, order: {self.order}}}"


def declaration(pid_def: Mapping[str, Any] | None) -> MapDecl | None:
    """The PID's declared map, or None. Raises :class:`SupportMapError` if malformed."""
    raw = (pid_def or {}).get("support_map")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SupportMapError("support_map must be a mapping")
    return MapDecl.from_mapping(raw)


@dataclass(frozen=True)
class ResolvedMap:
    """A declaration read off one payload. Positions are ISO-TP payload indices."""

    decl: MapDecl
    mask: bytes
    map_indices: tuple[int, ...]
    covered: range
    bit_marks: Mapping[int, frozenset[int]]  # bit unit: payload index -> marked data bits
    byte_marks: frozenset[int]  # byte unit: marked payload indices

    @property
    def mask_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.mask)

    def is_signal_byte(self, index: int) -> bool:
        """True if any part of the byte at ``index`` is marked."""
        if self.decl.unit == "bit":
            return bool(self.bit_marks.get(index))
        return index in self.byte_marks

    def signal_bits(self, index: int) -> frozenset[int]:
        """Marked bits of the byte at ``index`` (all eight when a per-byte mark covers it)."""
        if self.decl.unit == "bit":
            return self.bit_marks.get(index, frozenset())
        return frozenset(range(8)) if index in self.byte_marks else frozenset()

    def outside(self, index: int, bits: set[int]) -> tuple[set[int], set[int]]:
        """Split ``bits`` read at ``index`` into ``(outside the map, field continuations)``.

        A byte outside the covered range is not judged. In a per-byte map every bit of an
        unmarked byte is outside; in a per-bit map an unmarked bit directly above a mark counts
        as the rest of that field.
        """
        if index not in self.covered:
            return set(), set()
        if self.decl.unit == "byte":
            return (set() if index in self.byte_marks else set(bits)), set()
        marks = self.bit_marks.get(index, frozenset())
        cont = set(continuation_bits(marks, bits))
        return set(bits) - marks - cont, cont


def resolve(decl: MapDecl, payload: bytes, header: int) -> ResolvedMap | None:
    """Read ``decl`` off ``payload`` (SID first); None if the payload is too short.

    ``header`` is the SID plus DID/LID echo width (``1 + subfunction_bytes_for_pid(pid)``).
    """
    first = header + decl.at
    start = first + decl.length
    if start >= len(payload):
        return None
    mask = bytes(payload[first:start])
    if decl.unit == "bit":
        covered = range(start, min(start + decl.length, len(payload)))
        bit_marks = {i: marked_bits(mask[i - start]) for i in covered}
        return ResolvedMap(decl, mask, tuple(range(first, start)), covered, bit_marks, frozenset())
    covered = range(start, min(start + 8 * decl.length, len(payload)))
    marks = marked_byte_indices(mask, len(covered))
    return ResolvedMap(
        decl, mask, tuple(range(first, start)), covered, {}, frozenset(start + i for i in marks)
    )
