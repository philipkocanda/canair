"""Writing a dump in a format other tools open: Intel HEX, Motorola S-record, raw binary.

Intel HEX and S-record carry an address on every record, so a dump's holes stay holes.
A raw binary cannot say where a hole is, so it is written only for a dump with none in
its span, or with an explicit fill byte the caller chose.
"""

from __future__ import annotations

from collections.abc import Iterator

from .store import Dump, DumpError

_RECORD_BYTES = 16


def _chunks(dump: Dump) -> Iterator[tuple[int, bytes]]:
    for s in sorted(dump.stretches, key=lambda s: s.start):
        for i in range(0, len(s.data), _RECORD_BYTES):
            yield s.start + i, s.data[i : i + _RECORD_BYTES]


def _ihex_record(kind: int, address: int, data: bytes) -> str:
    body = bytes([len(data), (address >> 8) & 0xFF, address & 0xFF, kind]) + data
    checksum = (-sum(body)) & 0xFF
    return f":{body.hex().upper()}{checksum:02X}"


def to_ihex(dump: Dump) -> str:
    """The dump as Intel HEX (type-04 records for addresses above 64 KiB)."""
    lines: list[str] = []
    upper = None
    for address, data in _chunks(dump):
        # A record must not cross a 64 KiB boundary: split it there.
        while data:
            room = 0x10000 - (address & 0xFFFF)
            part, data = data[:room], data[room:]
            if address >> 16 != upper:
                upper = address >> 16
                if upper > 0xFFFF:
                    raise DumpError("Intel HEX addresses at most 4 GiB")
                lines.append(_ihex_record(0x04, 0, upper.to_bytes(2, "big")))
            lines.append(_ihex_record(0x00, address & 0xFFFF, part))
            address += len(part)
    lines.append(_ihex_record(0x01, 0, b""))
    return "\n".join(lines) + "\n"


def _srec_record(kind: int, address: int, data: bytes, address_bytes: int) -> str:
    body = bytes([address_bytes + len(data) + 1]) + address.to_bytes(address_bytes, "big") + data
    checksum = (~sum(body)) & 0xFF
    return f"S{kind}{body.hex().upper()}{checksum:02X}"


def to_srec(dump: Dump) -> str:
    """The dump as Motorola S-record (S3 data records with 32-bit addresses)."""
    header = (dump.id or dump.ecu).encode()[:40]
    lines = [_srec_record(0, 0, header, 2)]
    lines += [_srec_record(3, address, data, 4) for address, data in _chunks(dump)]
    lines.append(_srec_record(7, 0, b"", 4))
    return "\n".join(lines) + "\n"


def to_bin(dump: Dump, fill: int | None = None) -> tuple[int, bytes]:
    """``(first address, bytes)`` of the dump's whole span; a hole needs ``fill``."""
    stretches = sorted(dump.stretches, key=lambda s: s.start)
    if not stretches:
        raise DumpError("the dump holds no bytes")
    first, last = stretches[0].start, max(s.end for s in stretches)
    out = bytearray()
    cursor = first
    for s in stretches:
        if s.start > cursor:
            if fill is None:
                raise DumpError(
                    f"the dump has a hole at 0x{cursor:X}-0x{s.start - 1:X}; a binary cannot "
                    "show it — export ihex or srec, or pass --fill"
                )
            out += bytes([fill]) * (s.start - cursor)
        out += s.data
        cursor = s.end + 1
    assert cursor == last + 1
    return first, bytes(out)
