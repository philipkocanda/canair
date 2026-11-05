"""Comparing two dumps of one ECU: the address runs whose bytes differ.

The RE loop it serves: dump RAM in two vehicle states, diff, and the runs that changed are
the candidate variables. Two dumps from different software builds are refused, since a
reflash moves what an address holds and every byte could differ for that reason alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import Dump, DumpError


@dataclass(frozen=True)
class DiffRun:
    """Consecutive addresses whose bytes differ, with both dumps' bytes."""

    start: int
    a: bytes
    b: bytes

    @property
    def end(self) -> int:
        return self.start + len(self.a) - 1


@dataclass(frozen=True)
class DumpDiff:
    runs: list[DiffRun]
    compared: int  # addresses both dumps read
    only_a: int  # addresses only the first read
    only_b: int


def diff_dumps(a: Dump, b: Dump, *, min_run: int = 1) -> DumpDiff:
    """The runs of at least ``min_run`` differing bytes between ``a`` and ``b``.

    Raises :class:`DumpError` for dumps of different ECUs or software builds.
    """
    if a.ecu.upper() != b.ecu.upper():
        raise DumpError(f"the dumps are of different ECUs ({a.ecu}, {b.ecu})")
    if a.software and b.software and a.software != b.software:
        raise DumpError(
            f"the dumps are of different software builds ({a.software}, {b.software}): "
            "a reflash moves what an address holds"
        )
    ma, mb = a.byte_map(), b.byte_map()
    common = sorted(ma.keys() & mb.keys())
    runs: list[DiffRun] = []
    start: int | None = None
    prev = None
    bytes_a: list[int] = []
    bytes_b: list[int] = []

    def close() -> None:
        if start is not None and len(bytes_a) >= min_run:
            runs.append(DiffRun(start, bytes(bytes_a), bytes(bytes_b)))

    for addr in common:
        if ma[addr] == mb[addr]:
            continue
        if start is not None and prev is not None and addr == prev + 1:
            bytes_a.append(ma[addr])
            bytes_b.append(mb[addr])
        else:
            close()
            start, bytes_a, bytes_b = addr, [ma[addr]], [mb[addr]]
        prev = addr
    close()
    return DumpDiff(runs, len(common), len(ma.keys() - mb.keys()), len(mb.keys() - ma.keys()))
