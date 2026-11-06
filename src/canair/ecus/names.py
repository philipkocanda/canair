"""ECU names and aliases: the name indices, canonical names, ``resolve_tx`` and the label."""

from collections.abc import Mapping

from canair.ecus.registry import EcuRegistryEntry, build_rx_tx_index, load_ecus, rx_addr_str
from canair.profile.model import Profile


def ecu_name(
    tx_id: int, ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> str:
    """Get ECU name for a TX ID, or '0x{tx_id:03X}' if unknown."""
    if ecus is None:
        ecus = load_ecus(profile)
    info = ecus.get(tx_id)
    return info["name"] if info else f"0x{tx_id:03X}"


class EcuNameCollision(ValueError):
    """Raised when an ECU name or alias resolves ambiguously to >1 ECU.

    Guards the name/alias lookup so an ambiguous registry (two ECUs sharing an
    alias, or an alias equal to a different ECU's name) fails loudly instead of
    silently resolving to the wrong module.
    """


def build_name_tx_index(
    ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> dict[str, int]:
    """Build lookup: upper-cased ECU name (and alias) -> TX id.

    Primary ``name`` keys are assigned first (authoritative), then ``alias``
    keys. Raises :class:`EcuNameCollision` if any name/alias is claimed by more
    than one ECU, so aliases can be trusted for lookups without silently masking
    the wrong ECU.
    """
    if ecus is None:
        ecus = load_ecus(profile)
    index: dict[str, int] = {}
    # Two passes so a primary name always wins its key and an alias clashing
    # with a *different* ECU's name/alias is detected regardless of iteration
    # order.
    for field in ("name", "alias"):
        for tx_id, info in ecus.items():
            value = info.get(field)
            if not value:
                continue
            key = str(value).upper()
            prev = index.get(key)
            if prev is not None and prev != tx_id:
                raise EcuNameCollision(
                    f"ECU {field} {value!r} is claimed by both "
                    f"{ecu_name(prev, ecus, profile=profile)} (0x{prev:03X}) and "
                    f"{ecu_name(tx_id, ecus, profile=profile)} (0x{tx_id:03X})"
                )
            index[key] = tx_id
    return index


def build_canonical_name_index(
    ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> dict[str, str]:
    """Build lookup: upper-cased ECU name/alias -> canonical short name.

    Both the primary ``name`` and any ``alias`` resolve to the canonical
    ``name``. Reuses :func:`build_name_tx_index`, so it inherits the same
    collision safety (raises :class:`EcuNameCollision` on ambiguity).
    """
    if ecus is None:
        ecus = load_ecus(profile)
    return {
        key: ecu_name(tx_id, ecus, profile=profile)
        for key, tx_id in build_name_tx_index(ecus, profile=profile).items()
    }


def build_alias_index(
    ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> dict[str, str]:
    """Map canonical ECU short name -> its declared alias (only ECUs that have one).

    Used for display (e.g. the captures stepper shows ``SKM (alias SMK)``) so the
    secondary name an ECU self-identifies as is visible alongside the canonical
    short name.
    """
    if ecus is None:
        ecus = load_ecus(profile)
    out: dict[str, str] = {}
    for info in ecus.values():
        name = info.get("name")
        alias = info.get("alias")
        if name and alias:
            out[name] = alias
    return out


def canonical_ecu_name(
    name,
    name_index: dict[str, str] | None = None,
    ecus: Mapping[int, EcuRegistryEntry] | None = None,
    *,
    profile: Profile,
) -> str:
    """Resolve an ECU name/alias to its canonical short name.

    Accepts a name or alias (case-insensitive). Unknown values are returned
    upper-cased and unchanged, so callers can still match/report them. Raises
    :class:`EcuNameCollision` (via the index) if the registry is ambiguous.
    """
    if name is None:
        return ""
    if name_index is None:
        name_index = build_canonical_name_index(ecus, profile=profile)
    key = str(name).strip().upper()
    return name_index.get(key, key)


def canonical_ecu_name_safe(
    name,
    name_index: dict[str, str] | None = None,
    ecus: Mapping[int, EcuRegistryEntry] | None = None,
    *,
    profile: Profile,
) -> str:
    """:func:`canonical_ecu_name` that tolerates a missing ECU registry.

    Resolves a name/alias to its canonical short name (case-insensitive),
    falling back to the upper-cased input when the ECU registry is absent (a
    profile may ship ``ecus/`` without full identity blocks). An ambiguous registry
    (:class:`EcuNameCollision`) still propagates, matching the ``captures``
    selector behaviour.
    """
    try:
        return canonical_ecu_name(name, name_index=name_index, ecus=ecus, profile=profile)
    except FileNotFoundError:
        return "" if name is None else str(name).strip().upper()


def rx_from_name(
    name: str, name_index: dict[str, int] | None = None, *, profile: Profile
) -> str | None:
    """Resolve an ECU name/alias to its RX response-address string, or None."""
    if name_index is None:
        name_index = build_name_tx_index(profile=profile)
    tx_id = name_index.get(str(name).strip().upper())
    return rx_addr_str(tx_id, profile=profile) if tx_id is not None else None


def resolve_tx(
    value,
    name_index: dict[str, int] | None = None,
    ecus: Mapping[int, EcuRegistryEntry] | None = None,
    *,
    profile: Profile,
) -> int | None:
    """Resolve an ECU name/alias or hex TX/RX ID to a TX id int, or None.

    Accepts an ECU name/alias ('BMS', 'igpm'), a hex TX id ('7E4', '0x770'),
    or a hex RX id ('0x7EA' resolves to VCU's TX 0x7E2, via the resolved
    response-address map). A known TX id always wins over an RX interpretation
    of the same value.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if not s:
        return None
    if name_index is None:
        name_index = build_name_tx_index(profile=profile)
    tx_id = name_index.get(s.upper())
    if tx_id is not None:
        return tx_id
    try:
        parsed = int(s.removeprefix("0x").removeprefix("0X"), 16)
    except ValueError:
        return None
    if ecus is None:
        ecus = load_ecus(profile)
    # A hex value is a TX id if it's a registered ECU; otherwise fall back to
    # interpreting it as an RX (response) address and resolving to its TX id.
    if parsed not in ecus:
        rx_tx = build_rx_tx_index(ecus, profile=profile)
        if parsed in rx_tx:
            return rx_tx[parsed]
    return parsed


def ecu_display(
    tx_id: int, ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> str:
    """Human-friendly label for a TX id, e.g. 'BMS (0x7E4)' or '0x7E4'."""
    name = ecu_name(tx_id, ecus, profile=profile)
    return f"0x{tx_id:03X}" if name.startswith("0x") else f"{name} (0x{tx_id:03X})"
