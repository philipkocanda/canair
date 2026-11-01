"""OBD-II (SAE J1979) facts the generic UDS layer does not carry.

Mode 01 PIDs ``00``, ``20``, ``40``, … (every multiple of 0x20) answer a 4-byte
bitmap of which of the next 32 PIDs the ECU supports: the most significant bit of
the first byte is PID ``base + 1``, the least significant bit of the last is
``base + 0x20``, which is itself the next bitmap PID.
"""

from __future__ import annotations

SUPPORT_BITMAP_STEP = 0x20


def is_support_bitmap_pid(pid: int) -> bool:
    """True for the mode 01 PIDs that answer a supported-PID bitmap."""
    return 0 <= pid <= 0xE0 and pid % SUPPORT_BITMAP_STEP == 0


def supported_pids(base: int, bitmap: bytes) -> list[int]:
    """The PIDs a 4-byte support ``bitmap`` read from PID ``base`` advertises."""
    bits = int.from_bytes(bitmap[:4], "big")
    return [base + i + 1 for i in range(32) if bits & (1 << (31 - i))]
