"""The ECU registry and its response addresses: ``load_ecus``, TX to RX and identity."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict, cast

from canair.profile.model import Profile, ProfileError
from canair.uds.addressing import (
    DEFAULT_MODE,
    DEFAULT_RX_OFFSET,
    AddressingMode,
    EcuAddress,
    resolve_ecu_address,
    resolve_mode,
    resolve_rx,
    resolve_rx_offset,
)
from canair.uds.protocol import UNKNOWN, classify_protocol

# Non-address ECU references allowed in capture files (multi-ECU / broadcast
# captures that don't map to a single physical responder).
SENTINELS = frozenset({"broadcast"})


class _EcuIdentity(TypedDict, total=False):
    """Optional identity fields flattened onto a registry entry.

    Mirrors ``identity_fields.optional`` in ``src/canair/schema/pids_schema.yaml`` —
    the ECU's self-reported identity block, plus the top-level ``can_bus`` list.
    All optional (an identity-only or barely-probed ECU may carry none).
    """

    alias: str
    description: str
    part_number: str
    mfg_date: str
    hw_version: str
    sw_version: str
    hw_sw: str
    boot_sw: str
    app_sw: str
    fw_version: str
    firmware: str
    serial: str
    ecu_id: str
    sw_id: str
    calibration: str
    supplier: str
    diag_address: str
    vin: str
    id_protocol: str
    identity_confidence: str
    notes: str
    can_bus: list[str]


class EcuRegistryEntry(_EcuIdentity):
    """One ECU's entry in a :func:`load_ecus` result (keyed by TX id).

    ``name`` (the canonical short name — the ECU file's top key), the resolved
    CAN response address ``rx_id``, the resolved addressing ``mode``, and the full
    resolved ``address`` (:class:`~canair.uds.addressing.EcuAddress`) are always
    present; the identity fields from :class:`_EcuIdentity` are optional. The
    registry is keyed by TX id, so ``tx_id`` itself is the dict key rather than a
    field here.
    """

    name: str
    rx_id: int
    mode: AddressingMode
    address: EcuAddress


def load_ecus(profile: Profile) -> dict[int, EcuRegistryEntry]:
    """Load the ECU registry, keyed by TX id (int) -> {name, identity fields...}.

    Derived from the profile's per-ECU ``ecus/`` files: each ECU's short name
    (the file's top-level key) plus its flattened ``identity:`` block.
    """
    # deferred: cycle, pids imports names, which imports this module
    from canair.ecus.pids import load_pids

    return _registry_of(load_pids(profile))


def load_ecus_dir(path: Path) -> dict[int, EcuRegistryEntry]:
    """:func:`load_ecus` for a directory of per-ECU YAML files, as written."""
    # deferred: cycle, pids imports names, which imports this module
    from canair.ecus.pids import load_pids_dir

    return _registry_of(load_pids_dir(path))


def _registry_of(data: dict) -> dict[int, EcuRegistryEntry]:
    result: dict[int, EcuRegistryEntry] = {}
    for name, ecu_def in (data.get("ecus") or {}).items():
        if not isinstance(ecu_def, dict):
            continue
        tx_id = ecu_def.get("tx_id")
        if tx_id is None:
            continue
        tx_id = int(tx_id)
        # The identity block is flattened onto the entry (arbitrary self-reported
        # fields), so it's built as a dynamic dict and cast to the documented
        # shape at the boundary — mirrors capture_io's parsed-JSON → TypedDict seam.
        info: dict[str, Any] = {"name": name}
        identity = ecu_def.get("identity")
        if isinstance(identity, dict):
            info.update(identity)
        # can_bus is a top-level ECU field (sibling of identity); surface it too.
        can_bus = ecu_def.get("can_bus")
        if can_bus is not None:
            info["can_bus"] = can_bus
        # Resolve the full CAN addressing once (mode + RX + any extended/flow
        # -control bytes) so every consumer reads it rather than recomputing +8.
        # The resolved RX and mode are also surfaced flat for the many callers
        # that only need those two; the transport reads the whole EcuAddress.
        address = resolve_ecu_address(data, ecu_def)
        info["address"] = address
        info["rx_id"] = address.rx_id
        info["mode"] = address.mode
        result[tx_id] = cast(EcuRegistryEntry, info)
    return result


# Evidence markers in a note that indicate the identity rests on weak, external
# grounds (cross-vehicle reference) rather than this car's own data.
_WEAK_IDENTITY_MARKERS = (
    "speculative",
    "address match",
    "address proximity",
    "spreadsheet",
    "cross-platform reference",
    "cross-vehicle",
    "no evidence",
)


# Self-reported identity fields — presence means the ECU answered an identity
# request with real data (grounds the identity in the module itself).
_IDENTITY_EVIDENCE_FIELDS = (
    "part_number",
    "serial",
    "sw_id",
    "ecu_id",
    "app_sw",
    "calibration",
    "hw_sw",
    "fw_version",
    "firmware",
    "sw_version",
    "hw_version",
)


def derive_identity_confidence(info: EcuRegistryEntry) -> str:
    """Heuristically rate how well-established an ECU's identity is.

    Returns one of ``confirmed`` / ``probable`` / ``tentative`` / ``speculative``
    (see ``valid_identity_confidence`` in pids_schema.yaml). Used when an entry
    has no explicit ``identity_confidence``.
    """
    name = str(info.get("name") or "")
    notes = str(info.get("notes") or "").lower()
    proto = classify_protocol(info.get("id_protocol"))
    has_part_number = bool(info.get("part_number"))
    has_identity = any(info.get(k) for k in _IDENTITY_EVIDENCE_FIELDS)

    if name.lower().startswith("unknown"):
        return "speculative"
    if any(marker in notes for marker in _WEAK_IDENTITY_MARKERS):
        return "tentative"
    if has_part_number:
        return "confirmed"
    if has_identity and proto != UNKNOWN:
        return "probable"
    return "tentative"


def ecu_identity_confidence(info: EcuRegistryEntry) -> tuple[str, bool]:
    """Return ``(confidence, explicit)`` for an ECU registry entry.

    ``explicit`` is True when the value is set in the registry, False when it was
    derived by :func:`derive_identity_confidence`.
    """
    explicit = info.get("identity_confidence")
    if explicit:
        return explicit, True
    return derive_identity_confidence(info), False


def rx_for_tx(
    tx_id: int,
    ecus: Mapping[int, EcuRegistryEntry] | None = None,
    rx_offset: int | None = None,
    mode: AddressingMode | None = None,
    *,
    profile: Profile,
) -> int:
    """Resolve a TX id to its CAN response (RX) address int.

    Uses the ECU registry's resolved ``rx_id`` (which already honors a per-ECU
    ``rx_id`` override or the profile's ``addressing.rx_offset``) when the ECU
    is known; otherwise falls back to :func:`~canair.uds.addressing.resolve_rx` — the
    escape hatch for an address not (yet) in the registry, e.g. an unregistered
    responder seen during discovery or a ``raw`` read. That fallback applies the
    given ``rx_offset``/``mode``, or the active profile's ``addressing:`` block
    for whichever is omitted, so an unknown address on a non-``+0x08`` or 29-bit
    profile resolves the way a registered one would.
    """
    if ecus is None:
        ecus = load_ecus(profile)
    info = ecus.get(tx_id)
    if info and info.get("rx_id") is not None:
        return int(info["rx_id"])
    if rx_offset is None or mode is None:
        default_offset, default_mode = _profile_addressing(profile)
        rx_offset = default_offset if rx_offset is None else rx_offset
        mode = default_mode if mode is None else mode
    return resolve_rx(tx_id, rx_offset=rx_offset, mode=mode)


def _profile_addressing(profile: Profile) -> tuple[int, AddressingMode]:
    """``profile``'s default ``(rx_offset, mode)``, or the built-in defaults.

    Falls back to ``TX + 0x08`` / 11-bit when no profile resolves, so a caller
    that passed its own registry still gets an answer outside a profile.
    """
    # deferred: cycle, pids imports names, which imports this module
    from canair.ecus.pids import load_pids

    try:
        data = load_pids(profile)
    except ProfileError:
        return DEFAULT_RX_OFFSET, DEFAULT_MODE
    return resolve_rx_offset(data), resolve_mode(data)


def rx_addr_str(
    tx_id: int, ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> str:
    """Format the CAN response address for a TX id as a hex string.

    Resolved by :func:`rx_for_tx`: a registered ECU's ``rx_id``, else the active
    profile's ``addressing:`` rule for an unknown address, e.g.
    ``rx_addr_str(0x7E4) == "0x7EC"`` on a ``+0x08`` profile.
    """
    return f"0x{rx_for_tx(tx_id, ecus, profile=profile):03X}"


def parse_ecu_ref(value) -> int | None:
    """Parse a capture ``ecu`` reference into an RX address int, or None.

    Accepts hex strings (``"0x7EC"``, ``"7EC"``) and ints. Returns ``None`` for
    sentinels (e.g. ``"broadcast"``) and anything unparseable.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if not s or s.lower() in SENTINELS:
        return None
    try:
        return int(s, 16)
    except ValueError:
        return None


def build_rx_index(
    ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> dict[int, str]:
    """Build lookup: RX address (int) -> canonical ECU short name.

    Derived from the ECU registry (the superset of all known addresses, including
    non-decodable modules like AMP/SRS), so any responder resolves for display.
    Uses each ECU's resolved ``rx_id`` (per-ECU override / profile offset).
    """
    if ecus is None:
        ecus = load_ecus(profile)
    return {rx_for_tx(tx_id, ecus, profile=profile): info["name"] for tx_id, info in ecus.items()}


def build_rx_tx_index(
    ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> dict[int, int]:
    """Build lookup: RX address (int) -> TX id, using each ECU's resolved rx_id."""
    if ecus is None:
        ecus = load_ecus(profile)
    return {rx_for_tx(tx_id, ecus, profile=profile): tx_id for tx_id in ecus}


def ecu_name_from_ref(value, rx_index: dict[int, str] | None = None, *, profile: Profile) -> str:
    """Resolve a capture ``ecu`` reference (RX address) to a canonical name.

    Sentinels and unresolvable references are returned unchanged (stringified),
    so callers always get a printable label.
    """
    if rx_index is None:
        rx_index = build_rx_index(profile=profile)
    rx = parse_ecu_ref(value)
    if rx is not None and rx in rx_index:
        return rx_index[rx]
    return str(value)
