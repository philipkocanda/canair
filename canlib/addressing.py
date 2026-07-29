"""ECU CAN addressing: mode (11-bit / 29-bit) and response-address resolution.

Two make-specific facts about how a diagnostic request reaches an ECU and how
its response is addressed live here, so neither leaks as a constant across the
raw transport, the ECU registry, discovery, and validation:

**1. The addressing mode** — how the CAN arbitration ID(s) are formed. Most
Hyundai/Kia ECUs use 11-bit "normal" addressing (a plain ``0x7E4`` request id);
many other makes (Ford, VAG, …) use the 29-bit *normal fixed* diagnostic
convention (``0x18DA{target}{tester}`` request, ``0x18DA{tester}{target}``
response, ``0x18DB33F1`` functional broadcast). :class:`AddressingMode` is the
canonical vocabulary; :func:`resolve_mode` reads it from a profile/ECU, and
:func:`build_isotp_address` turns ``(tx_id, rx_id, mode)`` into the ``isotp``
address object both raw clients drive their ISO-TP stacks with.

**2. The TX→RX offset** — for the 11-bit modes the response is conventionally
``TX + 0x08`` (Hyundai/Kia), but that offset is make-specific: some vehicles use
a different fixed offset (the XPeng G6's ``TX + 0x80``, ``0x704`` → ``0x784``) or
an irregular per-ECU mapping. For the 29-bit *normal fixed* mode the response id
is derived by swapping the target/tester address bytes, not by an offset.

Resolution precedence for one ECU's RX address (:func:`resolve_rx`):

1. an explicit per-ECU ``rx_id`` (from the ECU file), else
2. for :attr:`AddressingMode.NORMAL_FIXED_29BIT`, the byte-swapped fixed-29-bit
   response id, else
3. ``tx_id + rx_offset`` where ``rx_offset`` is the profile's
   ``addressing.rx_offset`` (``profile.yaml``), else
4. the conventional default :data:`DEFAULT_RX_OFFSET` (``0x08``).

See ``plans/2026-07-28-multi-vehicle-support.md`` (Phases 2–3).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import isotp

# Conventional 11-bit UDS response offset (0x770 → 0x778, 0x7E4 → 0x7EC). Used
# when a profile declares no addressing.rx_offset and an ECU has no rx_id.
DEFAULT_RX_OFFSET: Final = 0x08

# 29-bit functional (broadcast) diagnostic request id (ISO 15765-4): a request
# on this id is heard by every ECU. Physical requests use 0x18DA{target}{tester}.
FUNCTIONAL_29BIT_ID: Final = 0x18DB33F1


class AddressingMode(StrEnum):
    """How an ECU's CAN diagnostic arbitration IDs are formed.

    Values are the tokens written in a profile/ECU ``addressing.mode`` field.
    ``StrEnum`` so a plain string comparison / YAML round-trip just works.
    """

    NORMAL_11BIT = "normal_11bit"  # plain 11-bit ids (Hyundai/Kia default)
    NORMAL_29BIT = "normal_29bit"  # arbitrary 29-bit tx/rx ids (needs explicit rx_id)
    NORMAL_FIXED_29BIT = "normal_fixed_29bit"  # 0x18DA{ta}{sa} diagnostic convention
    EXTENDED_29BIT = "extended_29bit"  # 29-bit + target-address extension byte


DEFAULT_MODE: Final = AddressingMode.NORMAL_11BIT

# Modes whose arbitration IDs are 29-bit (extended CAN frames).
_29BIT_MODES: Final = frozenset(
    {
        AddressingMode.NORMAL_29BIT,
        AddressingMode.NORMAL_FIXED_29BIT,
        AddressingMode.EXTENDED_29BIT,
    }
)


def parse_mode(value: Any) -> AddressingMode | None:
    """Coerce a string/enum to an :class:`AddressingMode`, or None if unknown."""
    if isinstance(value, AddressingMode):
        return value
    if isinstance(value, str):
        try:
            return AddressingMode(value.strip().lower())
        except ValueError:
            return None
    return None


def is_extended(mode: AddressingMode) -> bool:
    """True when ``mode`` uses 29-bit (extended) CAN arbitration IDs."""
    return mode in _29BIT_MODES


def _mode_from_block(block: Any) -> AddressingMode | None:
    """Extract an addressing mode from an ``addressing:`` mapping, if present/valid."""
    if isinstance(block, Mapping):
        return parse_mode(block.get("mode"))
    return None


def resolve_mode(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any] | None = None,
) -> AddressingMode:
    """Resolve the addressing mode for an ECU.

    Precedence: a per-ECU ``addressing.mode`` (``ecu_def``) → the profile-wide
    ``addressing.mode`` (``meta``) → :data:`DEFAULT_MODE` (11-bit). An
    unrecognized value is ignored (falls through), matching the permissive
    posture of :func:`resolve_rx_offset`.
    """
    if ecu_def is not None:
        mode = _mode_from_block(ecu_def.get("addressing"))
        if mode is not None:
            return mode
    if isinstance(meta, Mapping):
        mode = _mode_from_block(meta.get("addressing"))
        if mode is not None:
            return mode
    return DEFAULT_MODE


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


def fixed_29bit_rx(tx_id: int) -> int:
    """Response id for a normal-fixed 29-bit request (swap the low two bytes).

    A physical request ``0x18DA{target}{tester}`` is answered on
    ``0x18DA{tester}{target}`` — i.e. the low two address bytes swap while the
    priority/format prefix (upper bits) is preserved.
    """
    ta = (tx_id >> 8) & 0xFF
    sa = tx_id & 0xFF
    return (tx_id & ~0xFFFF) | (sa << 8) | ta


def resolve_rx(
    tx_id: int,
    rx_id: int | None = None,
    rx_offset: int = DEFAULT_RX_OFFSET,
    mode: AddressingMode = DEFAULT_MODE,
) -> int:
    """Resolve an ECU's CAN response (RX) address.

    An explicit ``rx_id`` always wins. Otherwise the normal-fixed 29-bit mode
    derives it by swapping the address bytes (:func:`fixed_29bit_rx`); every
    other mode uses ``tx_id + rx_offset``.
    """
    if rx_id is not None:
        return rx_id
    if mode == AddressingMode.NORMAL_FIXED_29BIT:
        return fixed_29bit_rx(tx_id)
    return tx_id + rx_offset


def build_isotp_address(
    tx_id: int,
    rx_id: int,
    mode: AddressingMode = DEFAULT_MODE,
) -> isotp.Address:
    """Build the ``isotp.Address`` for one ECU given its ids + addressing mode.

    The single home for turning canair's ``(tx, rx, mode)`` into the ``isotp``
    library's address object, so neither raw client (``uds_raw`` / ``raw_terminal``)
    hardwires ``Normal_11bits``. For the fixed/extended 29-bit modes the
    target/source address bytes are taken from the request id
    (``0x18DA{target}{tester}`` → target = bits 8–15, tester = bits 0–7).
    """
    import isotp

    if mode == AddressingMode.NORMAL_11BIT:
        return isotp.Address(isotp.AddressingMode.Normal_11bits, txid=tx_id, rxid=rx_id)
    if mode == AddressingMode.NORMAL_29BIT:
        return isotp.Address(isotp.AddressingMode.Normal_29bits, txid=tx_id, rxid=rx_id)
    target = (tx_id >> 8) & 0xFF
    source = tx_id & 0xFF
    if mode == AddressingMode.NORMAL_FIXED_29BIT:
        return isotp.Address(
            isotp.AddressingMode.NormalFixed_29bits,
            target_address=target,
            source_address=source,
        )
    if mode == AddressingMode.EXTENDED_29BIT:
        return isotp.Address(
            isotp.AddressingMode.Extended_29bits,
            txid=tx_id,
            rxid=rx_id,
            target_address=target,
            source_address=source,
        )
    raise ValueError(f"unsupported addressing mode: {mode!r}")
