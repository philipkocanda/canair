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
:func:`build_isotp_address` turns a resolved :class:`EcuAddress` into the
``isotp`` address object both raw clients drive their ISO-TP stacks with. Two
further make-specific schemes are modelled per ECU on top of the mode: the
ISO-TP *extended (mixed) 11-bit* target-address byte (BMW/PSA 0x6F1, gap G-I)
and a *flow-control address override* for functional-TX / physical-RX ECUs
(Renault/Mitsubishi, gap G-J) — both carried on :class:`EcuAddress`.

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
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, assert_never

if TYPE_CHECKING:
    import isotp

# Conventional 11-bit UDS response offset (0x770 → 0x778, 0x7E4 → 0x7EC). Used
# when a profile declares no addressing.rx_offset and an ECU has no rx_id.
DEFAULT_RX_OFFSET: Final = 0x08

# 29-bit functional (broadcast) diagnostic request id (ISO 15765-4): a request
# on this id is heard by every ECU. Physical requests use 0x18DA{target}{tester}.
FUNCTIONAL_29BIT_ID: Final = 0x18DB33F1

# Conventional diagnostic tester (source) address — the "F1" in 0x18DA{ta}F1 and
# the ISO-TP extended-11-bit source byte the tester answers to. Overridable per
# profile/ECU (BMW/PSA tester schemes all use 0xF1, but the field stays explicit).
DEFAULT_TESTER_ADDRESS: Final = 0xF1


class AddressingMode(StrEnum):
    """How an ECU's CAN diagnostic arbitration IDs are formed.

    Values are the tokens written in a profile/ECU ``addressing.mode`` field.
    ``StrEnum`` so a plain string comparison / YAML round-trip just works.
    """

    NORMAL_11BIT = "normal_11bit"  # plain 11-bit ids (Hyundai/Kia default)
    NORMAL_29BIT = "normal_29bit"  # arbitrary 29-bit tx/rx ids (needs explicit rx_id)
    NORMAL_FIXED_29BIT = "normal_fixed_29bit"  # 0x18DA{ta}{sa} diagnostic convention
    # ISO-TP extended (mixed) 11-bit: an 11-bit arbitration id (BMW 0x6F1 tester
    # scheme) PLUS a target-address extension byte carried as the first payload
    # byte. The extension byte varies per module (BMW 07/18/60, PSA/Stellantis),
    # so it is a per-ECU `addressing.target_address`; the tester answers to a
    # `source_address` (0xF1). See gap G-I in the multi-vehicle plan.
    NORMAL_EXTENDED_11BIT = "normal_extended_11bit"
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


def _addressing_block(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any] | None,
    key: str,
) -> Any:
    """Read ``addressing.<key>`` with per-ECU → profile precedence (or None)."""
    for scope in (ecu_def, meta):
        if isinstance(scope, Mapping):
            block = scope.get("addressing")
            if isinstance(block, Mapping) and block.get(key) is not None:
                return block[key]
    return None


def _addressing_int(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any] | None,
    key: str,
) -> int | None:
    """A per-ECU/profile ``addressing.<key>`` byte/id, if a plain int (not bool)."""
    value = _addressing_block(meta, ecu_def, key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def resolve_target_address(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any] | None = None,
) -> int | None:
    """The ISO-TP target-address extension byte (``addressing.target_address``).

    Per-ECU value → profile default → None. Only meaningful for the extended
    (mixed) modes (BMW/PSA extended 11-bit, extended 29-bit) where the target
    address rides in the payload rather than the arbitration id.
    """
    return _addressing_int(meta, ecu_def, "target_address")


def resolve_source_address(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any] | None = None,
    mode: AddressingMode = DEFAULT_MODE,
) -> int | None:
    """The ISO-TP tester (source) address (``addressing.source_address``).

    Per-ECU value → profile default → :data:`DEFAULT_TESTER_ADDRESS` (0xF1) for
    the extended *11-bit* scheme, else None. Only the extended-11-bit (BMW 0x6F1)
    scheme carries the source out-of-band and so needs a default; the 29-bit
    modes encode the tester byte in the arbitration id, so ``None`` there lets
    :func:`build_isotp_address` derive it from the id.
    """
    explicit = _addressing_int(meta, ecu_def, "source_address")
    if explicit is not None:
        return explicit
    if mode == AddressingMode.NORMAL_EXTENDED_11BIT:
        return DEFAULT_TESTER_ADDRESS
    return None


def resolve_fc_id(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any] | None = None,
) -> int | None:
    """A per-ECU flow-control arbitration-id override (``addressing.fc_id``).

    For a functional-TX / physical-RX ECU (Renault, Mitsubishi Outlander) the
    request goes to the functional broadcast id but ISO-TP flow control must be
    addressed to the ECU's *physical* request id. can-isotp otherwise sends flow
    control to the TX id, so this override redirects it. See gap G-J in the
    multi-vehicle plan. None (the common case) leaves flow control on the TX id.
    """
    return _addressing_int(meta, ecu_def, "fc_id")


@dataclass(frozen=True)
class EcuAddress:
    """The fully-resolved CAN addressing for one ECU.

    The single bundle threaded through the raw transport so the ISO-TP stack for
    an ECU is built from one value rather than a fistful of parallel maps. Built
    by :func:`resolve_ecu_address` from a profile + ECU definition; consumed by
    :func:`build_isotp_address` and the shared ISO-TP stack factory.

    ``target_address``/``source_address`` are the ISO-TP extension bytes for the
    extended (mixed) modes; ``fc_id`` overrides where flow-control frames are
    addressed (functional-TX ECUs). All three are None for the common 11-bit and
    normal-fixed-29-bit cases.
    """

    tx_id: int
    rx_id: int
    mode: AddressingMode = DEFAULT_MODE
    target_address: int | None = None
    source_address: int | None = None
    fc_id: int | None = None


def resolve_ecu_address(
    meta: Mapping[str, Any] | None,
    ecu_def: Mapping[str, Any],
) -> EcuAddress:
    """Resolve one ECU's complete :class:`EcuAddress` from profile + ECU data.

    ``meta`` is the profile-wide settings mapping (``profile.yaml`` merged into
    the loaded PID data); ``ecu_def`` is the per-ECU definition (carries ``tx_id``,
    optional ``rx_id``, and an optional ``addressing`` override block). Combines
    the mode, RX, and extended/flow-control resolvers into the single bundle the
    transport builds an ISO-TP stack from.
    """
    tx_id = int(ecu_def["tx_id"])
    mode = resolve_mode(meta, ecu_def)
    explicit_rx = ecu_def.get("rx_id")
    rx_id = resolve_rx(
        tx_id,
        int(explicit_rx) if explicit_rx is not None else None,
        resolve_rx_offset(meta),
        mode,
    )
    return EcuAddress(
        tx_id=tx_id,
        rx_id=rx_id,
        mode=mode,
        target_address=resolve_target_address(meta, ecu_def),
        source_address=resolve_source_address(meta, ecu_def, mode),
        fc_id=resolve_fc_id(meta, ecu_def),
    )


def build_isotp_address(addr: EcuAddress) -> isotp.Address:
    """Build the ``isotp.Address`` for one ECU from its resolved :class:`EcuAddress`.

    The single home for turning canair's addressing into the ``isotp`` library's
    address object, so neither raw client (``uds_raw`` / ``raw_terminal``)
    hardwires an addressing mode. For the fixed 29-bit mode the target/source
    bytes are taken from the request id (``0x18DA{target}{tester}``); the extended
    (mixed) modes take them from the resolved ``target_address``/``source_address``.
    """
    import isotp

    tx_id, rx_id, mode = addr.tx_id, addr.rx_id, addr.mode
    # 0x18DA{target}{tester}: target = bits 8-15, tester/source = bits 0-7.
    id_target = (tx_id >> 8) & 0xFF
    id_source = tx_id & 0xFF
    match mode:
        case AddressingMode.NORMAL_11BIT:
            return isotp.Address(isotp.AddressingMode.Normal_11bits, txid=tx_id, rxid=rx_id)
        case AddressingMode.NORMAL_29BIT:
            return isotp.Address(isotp.AddressingMode.Normal_29bits, txid=tx_id, rxid=rx_id)
        case AddressingMode.NORMAL_FIXED_29BIT:
            return isotp.Address(
                isotp.AddressingMode.NormalFixed_29bits,
                target_address=id_target,
                source_address=id_source,
            )
        case AddressingMode.NORMAL_EXTENDED_11BIT:
            # 11-bit arbitration ids + a payload target-address extension byte
            # (BMW/PSA 0x6F1 tester scheme). target/source come from the ECU
            # definition, not the arbitration id.
            if addr.target_address is None or addr.source_address is None:
                raise ValueError(
                    "normal_extended_11bit requires addressing.target_address "
                    "(and a tester source_address) — none resolved"
                )
            return isotp.Address(
                isotp.AddressingMode.Extended_11bits,
                txid=tx_id,
                rxid=rx_id,
                target_address=addr.target_address,
                source_address=addr.source_address,
            )
        case AddressingMode.EXTENDED_29BIT:
            return isotp.Address(
                isotp.AddressingMode.Extended_29bits,
                txid=tx_id,
                rxid=rx_id,
                target_address=addr.target_address
                if addr.target_address is not None
                else id_target,
                source_address=addr.source_address
                if addr.source_address is not None
                else id_source,
            )
    # Exhaustive over AddressingMode — a new member here is a type error at check
    # time (assert_never) rather than a silent runtime surprise.
    assert_never(mode)
