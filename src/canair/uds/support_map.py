"""A PID's declared support map: what the profile says, resolved against a real payload.

A PID may carry ``support_map: {at: 0, length: 4, unit: bit, order: msb}`` (see
``plans/2026-10-29-support-bitmap-coverage.md``): ``length`` constant bytes at data offset ``at``
(counted after the SID and DID echo) mark which of the data that follows are real signals.
``unit: field`` marks parameters of any width instead, and may count ``lsb`` first and name
further bitmaps in ``also_at`` (``plans/2026-11-03-support-map-field-maps.md``).
Only the *layout* is stored. The map bytes are read from each payload, so a declaration survives
an ECU software update that adds signals.

The marking rule itself lives in :mod:`canair.analysis.support_map` (one definition, shared with
detection); this module reads the declaration, validates it, and answers per-position questions
for ``coverage`` and ``bix``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from canair.uds.layout import subfunction_bytes_for_pid
from canair.uds.support_marks import (
    MAX_MAP_LEN,
    continuation_bits,
    marked_bits,
    marked_byte_indices,
)

UNITS: tuple[str, ...] = ("bit", "byte", "field")
ORDERS: tuple[str, ...] = ("msb", "lsb")
DECL_FIELDS = ("at", "length", "unit", "order", "also_at")
DeclUnit = Literal["bit", "byte", "field"]


class SupportMapError(ValueError):
    """A ``support_map:`` declaration that is malformed."""


@dataclass(frozen=True)
class MapDecl:
    """The layout of a map, as declared on a PID.

    ``unit: field`` is one map bit per parameter of the record, whatever its width
    (:mod:`canair.analysis.support_fields`); only it may count ``lsb`` first or name
    ``also_at``, the data offsets of the further bitmaps a record of more than ``8 * length``
    parameters carries.
    """

    at: int
    length: int
    unit: DeclUnit
    order: str = "msb"
    also_at: tuple[int, ...] = ()

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
            if not _is_int(value):
                raise SupportMapError(f"support_map {name} must be an integer")
        if at < 0:
            raise SupportMapError("support_map at must be >= 0")
        if not 1 <= length <= MAX_MAP_LEN:
            raise SupportMapError(f"support_map length must be 1..{MAX_MAP_LEN}")
        if unit not in UNITS:
            raise SupportMapError(f"support_map unit must be one of {', '.join(UNITS)}")
        if order not in ORDERS:
            raise SupportMapError(f"support_map order must be one of {', '.join(ORDERS)}")
        if order != "msb" and unit != "field":
            raise SupportMapError("support_map order lsb needs unit field")
        also = raw.get("also_at", [])
        if not isinstance(also, list | tuple) or not all(_is_int(v) for v in also):
            raise SupportMapError("support_map also_at must be a list of integers")
        if also and unit != "field":
            raise SupportMapError("support_map also_at needs unit field")
        floor = at + length
        for value in also:
            if value < floor:
                raise SupportMapError("support_map also_at must ascend past the leading map")
            floor = value + length
        return cls(at, length, unit, order, tuple(also))

    def flow(self) -> str:
        """The one-line flow-mapping form written into ``ecus/``."""
        also = f", also_at: [{', '.join(map(str, self.also_at))}]" if self.also_at else ""
        return (
            f"{{at: {self.at}, length: {self.length}, unit: {self.unit}, "
            f"order: {self.order}{also}}}"
        )

    @property
    def map_offsets(self) -> tuple[int, ...]:
        """Data offsets of every bitmap this declaration places."""
        return (self.at, *self.also_at)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def declaration(pid_def: Mapping[str, Any] | None) -> MapDecl | None:
    """The PID's declared map, or None. Raises :class:`SupportMapError` if malformed."""
    raw = (pid_def or {}).get("support_map")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SupportMapError("support_map must be a mapping")
    return MapDecl.from_mapping(raw)


def map_byte_indices(pid_def: Mapping[str, Any] | None, pid: str) -> tuple[int, ...]:
    """Payload indices (SID first) of the PID's declared map bytes, for display.

    Empty when the PID declares no map or a malformed one (``validate pids`` reports that); a
    renderer has nothing to do with the error.
    """
    try:
        decl = declaration(pid_def)
    except SupportMapError:
        return ()
    if decl is None:
        return ()
    header = 1 + subfunction_bytes_for_pid(pid)
    return tuple(header + o + k for o in decl.map_offsets for k in range(decl.length))


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
        """True if any part of the byte at ``index`` is marked.

        A field map cannot say without splitting the record into parameters (which needs the
        defined signals, :mod:`canair.analysis.support_fields`), so every covered byte that is
        not itself a bitmap may be one.
        """
        if self.decl.unit == "field":
            return index in self.covered and index not in self.map_indices
        if self.decl.unit == "bit":
            return bool(self.bit_marks.get(index))
        return index in self.byte_marks

    def signal_bits(self, index: int) -> frozenset[int]:
        """Marked bits of the byte at ``index`` (all eight when a per-byte mark covers it)."""
        if self.decl.unit == "field":
            return frozenset(range(8)) if self.is_signal_byte(index) else frozenset()
        if self.decl.unit == "bit":
            return self.bit_marks.get(index, frozenset())
        return frozenset(range(8)) if index in self.byte_marks else frozenset()

    def outside(self, index: int, bits: set[int]) -> tuple[set[int], set[int]]:
        """Split ``bits`` read at ``index`` into ``(outside the map, field continuations)``.

        A byte outside the covered range is not judged. In a per-byte map every bit of an
        unmarked byte is outside; in a per-bit map an unmarked bit directly above a mark counts
        as the rest of that field.
        """
        if index not in self.covered or self.decl.unit == "field":
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
    if decl.unit == "field":
        maps = tuple(
            i
            for o in decl.map_offsets
            for i in range(header + o, header + o + decl.length)
            if i < len(payload)
        )
        covered = range(start, len(payload))
        return ResolvedMap(decl, mask, maps, covered, {}, frozenset())
    if decl.unit == "bit":
        covered = range(start, min(start + decl.length, len(payload)))
        bit_marks = {i: marked_bits(mask[i - start]) for i in covered}
        return ResolvedMap(decl, mask, tuple(range(first, start)), covered, bit_marks, frozenset())
    covered = range(start, min(start + 8 * decl.length, len(payload)))
    marks = marked_byte_indices(mask, len(covered))
    return ResolvedMap(
        decl, mask, tuple(range(first, start)), covered, {}, frozenset(start + i for i in marks)
    )
