"""Broadcast-frame actions: the model and parsing for a profile's ``frames:`` requests.

A :class:`FrameRequest` is one non-diagnostic CAN frame a guarded action injects
(``slcan-tcp`` only). The safety story is in :mod:`canair.actions`; the rules enforced
here are structural, so ``canair validate actions`` catches a bad frame offline:

* the arbitration id is below :data:`DIAG_ID_FLOOR` (a frame can never forge a diagnostic
  response or an ISO-TP frame);
* every bit set in ``data`` — and the counter's nibble — lies inside ``mask`` (so every bit
  the action is not meant to drive is provably 0);
* a repeating frame is bounded by exactly one of ``count`` or ``duration_s``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from canair.actions.errors import ActionError

FRAME_FIELDS: Final = frozenset(
    {"id", "data", "mask", "counter", "rate_ms", "duration_s", "count", "bus"}
)
COUNTER_FIELDS: Final = frozenset({"byte", "nibble", "step"})

# The lowest diagnostic arbitration id (ES95486: 0x700-0x7FF). A frame action is refused at or
# above it, so it can never forge a diagnostic response or an ISO-TP frame.
DIAG_ID_FLOOR: Final = 0x700
# Bounds on a frame stream, so an action cannot flood the bus or run unbounded.
MIN_FRAME_RATE_MS: Final = 10
MAX_FRAME_DURATION_S: Final = 300.0
MAX_FRAME_COUNT: Final = 100_000

_HEX_RE = re.compile(r"^(?:[0-9A-F]{2})+$")


@dataclass(frozen=True)
class FrameCounter:
    """A free-running counter the sender advances by ``step`` each frame.

    ``byte`` is the payload byte (0-7); ``high`` selects its high nibble (bits 4-7),
    else its low nibble (bits 0-3). The 4-bit value wraps at 16. Its bits must lie
    inside the frame's ``mask``.
    """

    byte: int
    high: bool
    step: int = 1

    @property
    def nibble_mask(self) -> int:
        """The byte-value mask of the counter's nibble (``0xF0`` high, ``0x0F`` low)."""
        return 0xF0 if self.high else 0x0F


@dataclass(frozen=True)
class FrameRequest:
    """One broadcast CAN frame an action injects (non-diagnostic, ``slcan-tcp`` only).

    ``can_id`` is the arbitration id (refused at or above :data:`DIAG_ID_FLOOR`).
    ``data`` is the 8-byte template and ``mask`` the bits the frame is allowed to
    drive: every bit set in ``data`` (and the counter's nibble) must be inside it.
    ``rate_ms`` None means a single frame; otherwise the frame repeats every
    ``rate_ms`` until ``count`` frames or ``duration_s`` seconds. ``bus`` is a
    documentation note of where the author intends it to land.
    """

    can_id: int
    data: bytes
    mask: bytes
    counter: FrameCounter | None = None
    rate_ms: int | None = None
    duration_s: float | None = None
    count: int | None = None
    bus: str | None = None

    def describe(self) -> str:
        span = ""
        if self.rate_ms is not None:
            bound = f"{self.count}x" if self.count is not None else f"{self.duration_s:g}s"
            span = f" @{self.rate_ms}ms {bound}"
        return f"0x{self.can_id:X}:{self.data.hex().upper()}{span}"


def _frame_bytes(value: Any, field: str, where: str) -> bytes:
    """Parse an 8-byte hex payload (``data``/``mask``), tolerant of spaces."""
    text = str(value or "").replace(" ", "").upper()
    if not _HEX_RE.match(text) or len(text) != 16:
        raise ActionError(f"{where}: '{field}' must be 8 whole hex bytes, got {value!r}")
    return bytes.fromhex(text)


def _frame_counter(raw: Any, mask: bytes, where: str) -> FrameCounter | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ActionError(f"{where}: 'counter' must be a mapping with 'byte' and 'nibble'")
    unknown = set(raw) - COUNTER_FIELDS
    if unknown:
        raise ActionError(
            f"{where}: counter has unknown field(s) {', '.join(sorted(map(str, unknown)))}"
        )
    try:
        byte = int(raw["byte"])
    except (KeyError, TypeError, ValueError):
        raise ActionError(f"{where}: counter 'byte' must be 0-7, got {raw.get('byte')!r}") from None
    if not 0 <= byte <= 7:
        raise ActionError(f"{where}: counter 'byte' must be 0-7, got {byte}")
    nibble = str(raw.get("nibble") or "").strip().lower()
    if nibble not in ("high", "low"):
        raise ActionError(
            f"{where}: counter 'nibble' must be 'high' or 'low', got {raw.get('nibble')!r}"
        )
    try:
        step = int(raw.get("step", 1))
    except (TypeError, ValueError):
        raise ActionError(
            f"{where}: counter 'step' must be an integer, got {raw.get('step')!r}"
        ) from None
    counter = FrameCounter(byte, nibble == "high", step)
    if mask[byte] & counter.nibble_mask != counter.nibble_mask:
        raise ActionError(
            f"{where}: counter nibble (byte {byte} {nibble}) is outside 'mask', so the action "
            "is not allowed to drive those bits"
        )
    return counter


def _frame(raw: Any, where: str) -> FrameRequest:
    if not isinstance(raw, Mapping):
        raise ActionError(f"{where}: must be a mapping with 'id', 'data' and 'mask'")
    unknown = set(raw) - FRAME_FIELDS
    if unknown:
        raise ActionError(f"{where}: unknown field(s) {', '.join(sorted(map(str, unknown)))}")
    try:
        can_id = int(str(raw.get("id")), 0) if isinstance(raw.get("id"), str) else int(raw["id"])
    except (KeyError, TypeError, ValueError):
        raise ActionError(
            f"{where}: 'id' must be a CAN arbitration id, got {raw.get('id')!r}"
        ) from None
    if can_id < 0 or can_id >= DIAG_ID_FLOOR:
        raise ActionError(
            f"{where}: 'id' 0x{can_id:X} must be below 0x{DIAG_ID_FLOOR:X} — a broadcast frame "
            "may not use a diagnostic id (it could forge a diagnostic response)"
        )
    data = _frame_bytes(raw.get("data"), "data", where)
    mask = _frame_bytes(raw.get("mask"), "mask", where)
    outside = bytes(d & ~m for d, m in zip(data, mask, strict=True))
    if any(outside):
        raise ActionError(
            f"{where}: 'data' sets bits outside 'mask' ({outside.hex().upper()}): the action may "
            "only drive bits named in 'mask', so every other bit stays 0"
        )
    counter = _frame_counter(raw.get("counter"), mask, where)
    rate_ms, duration_s, count = _frame_stream(raw, where)
    bus = str(raw["bus"]).strip() if raw.get("bus") is not None else None
    return FrameRequest(can_id, data, mask, counter, rate_ms, duration_s, count, bus)


def _frame_stream(raw: Mapping, where: str) -> tuple[int | None, float | None, int | None]:
    """Parse and bound a frame's ``rate_ms``/``count``/``duration_s`` stream fields."""
    if raw.get("rate_ms") is None:
        if raw.get("count") is not None or raw.get("duration_s") is not None:
            raise ActionError(f"{where}: 'count'/'duration_s' need 'rate_ms' (a repeating frame)")
        return None, None, None
    try:
        rate_ms = int(raw["rate_ms"])
    except (TypeError, ValueError):
        raise ActionError(
            f"{where}: 'rate_ms' must be an integer, got {raw.get('rate_ms')!r}"
        ) from None
    if rate_ms < MIN_FRAME_RATE_MS:
        raise ActionError(f"{where}: 'rate_ms' must be at least {MIN_FRAME_RATE_MS}, got {rate_ms}")
    if (raw.get("count") is not None) == (raw.get("duration_s") is not None):
        raise ActionError(
            f"{where}: a repeating frame needs exactly one of 'count' or 'duration_s'"
        )
    count = duration_s = None
    if raw.get("count") is not None:
        try:
            count = int(raw["count"])
        except (TypeError, ValueError):
            raise ActionError(
                f"{where}: 'count' must be an integer, got {raw.get('count')!r}"
            ) from None
        if not 1 <= count <= MAX_FRAME_COUNT:
            raise ActionError(f"{where}: 'count' must be 1-{MAX_FRAME_COUNT}, got {count}")
    else:
        try:
            duration_s = float(raw["duration_s"])
        except (TypeError, ValueError):
            raise ActionError(
                f"{where}: 'duration_s' must be seconds, got {raw.get('duration_s')!r}"
            ) from None
        if not 0 < duration_s <= MAX_FRAME_DURATION_S:
            raise ActionError(
                f"{where}: 'duration_s' must be 0-{MAX_FRAME_DURATION_S:g} s, got {duration_s:g}"
            )
    return rate_ms, duration_s, count


def parse_frame_spec(spec: str) -> FrameRequest:
    """Parse the CLI form ``k=v,k=v,...`` (keys: id, data, mask, counter, rate_ms, …).

    ``counter`` is written ``BYTE:NIBBLE[:STEP]`` (e.g. ``7:low`` or ``7:low:1``). Example::

        id=0x587,data=1000000000000000,mask=100000000000000F,counter=7:low,rate_ms=100,duration_s=30
    """
    fields: dict[str, Any] = {}
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        key = key.strip()
        if not sep or key not in FRAME_FIELDS:
            raise ActionError(
                f"frame {spec!r}: bad field {part!r} (keys: {', '.join(sorted(FRAME_FIELDS))})"
            )
        fields[key] = value.strip()
    if "counter" in fields:
        bits = str(fields["counter"]).split(":")
        if len(bits) not in (2, 3):
            raise ActionError(f"frame {spec!r}: 'counter' is BYTE:NIBBLE[:STEP], e.g. 7:low")
        counter: dict[str, Any] = {"byte": bits[0], "nibble": bits[1]}
        if len(bits) == 3:
            counter["step"] = bits[2]
        fields["counter"] = counter
    return _frame(fields, f"frame {spec!r}")
