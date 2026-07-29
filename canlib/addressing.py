"""ECU CAN response-address resolution (TX → RX).

The diagnostic *response* (RX) address for a request (TX) is conventionally
``TX + 0x08`` on 11-bit Hyundai/Kia ECUs, but that offset is make-specific:
some vehicles use a different fixed offset (e.g. the XPeng G6's ``TX + 0x80``,
request ``0x704`` → response ``0x784``) or an irregular per-ECU mapping. This
module is the single home for that rule so ``RX = TX + 8`` stops being a
constant scattered across the raw transport, the ECU registry, discovery, and
validation.

Resolution precedence for one ECU's RX address:

1. an explicit per-ECU ``rx_id`` (from the ECU file), else
2. ``tx_id + rx_offset`` where ``rx_offset`` is the profile's
   ``addressing.rx_offset`` (``profile.yaml``), else
3. the conventional default :data:`DEFAULT_RX_OFFSET` (``0x08``).

29-bit / extended addressing modes are a later phase; this module only owns the
TX→RX offset today (see ``plans/2026-07-28-multi-vehicle-support.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# Conventional 11-bit UDS response offset (0x770 → 0x778, 0x7E4 → 0x7EC). Used
# when a profile declares no addressing.rx_offset and an ECU has no rx_id.
DEFAULT_RX_OFFSET: Final = 0x08


def resolve_rx_offset(meta: Mapping[str, Any] | None) -> int:
    """Profile default RX offset from ``meta['addressing']['rx_offset']``.

    ``meta`` is the profile-wide settings mapping (``profile.yaml`` contents,
    merged into the loaded PID data). Falls back to :data:`DEFAULT_RX_OFFSET`
    when the block or field is absent or malformed.
    """
    if isinstance(meta, Mapping):
        addressing = meta.get("addressing")
        if isinstance(addressing, Mapping):
            offset = addressing.get("rx_offset")
            # bool is an int subclass — reject it where a number is expected.
            if isinstance(offset, int) and not isinstance(offset, bool):
                return offset
    return DEFAULT_RX_OFFSET


def resolve_rx(
    tx_id: int,
    rx_id: int | None = None,
    rx_offset: int = DEFAULT_RX_OFFSET,
) -> int:
    """Resolve an ECU's CAN response (RX) address.

    An explicit ``rx_id`` always wins; otherwise ``tx_id + rx_offset``.
    """
    return rx_id if rx_id is not None else tx_id + rx_offset
