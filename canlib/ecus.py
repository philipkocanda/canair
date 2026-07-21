"""ECU address lookup and name/response-address resolution.

Backed by ``ecus.yaml`` (keyed by the OBD-II *request* arbitration ID, TX).
The CAN *response* address is ``RX = TX + 8``. Capture files reference the
**response** address as a hex string (e.g. ``"0x7EC"``); the helpers here
convert between that reference and the canonical ECU short name.
"""

from pathlib import Path

try:
    import yaml
except ImportError as e:
    raise ImportError("PyYAML not installed. Run: pip3 install pyyaml") from e

# Non-address ECU references allowed in capture files (multi-ECU / broadcast
# captures that don't map to a single physical responder).
SENTINELS = frozenset({"broadcast"})


def load_ecus(path: Path | None = None) -> dict:
    """Load ECU lookup table from YAML.

    Returns dict: tx_id (int) -> {name, description, ...}. When ``path`` is
    None, the active vehicle profile's ecus.yaml is used.
    """
    if path is None:
        from .profile import active

        path = active().ecus_file
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    result = {}
    for tx_id, info in (data.get("ecus") or {}).items():
        if isinstance(tx_id, str) and tx_id.startswith("0x"):
            tx_id = int(tx_id, 16)
        result[int(tx_id)] = info
    return result


def ecu_name(tx_id: int, ecus: dict | None = None) -> str:
    """Get ECU name for a TX ID, or '0x{tx_id:03X}' if unknown."""
    if ecus is None:
        ecus = load_ecus()
    info = ecus.get(tx_id)
    return info["name"] if info else f"0x{tx_id:03X}"


def ecu_id_protocol(tx_id: int, ecus: dict | None = None) -> str | None:
    """Return the identity protocol hint for a TX ID from the registry.

    One of ``"UDS"``, ``"KWP2000"``, ``"none"``, ``"unknown"``, or ``None`` when
    the ECU is not in the registry or has no ``id_protocol`` recorded. Used by
    ``canair identity`` to pick the right identity service without probing.
    """
    if ecus is None:
        ecus = load_ecus()
    info = ecus.get(tx_id)
    if not info:
        return None
    return info.get("id_protocol")


def rx_addr_str(tx_id: int) -> str:
    """Format the CAN response address (RX = TX + 8) as a hex string.

    e.g. ``rx_addr_str(0x7E4) == "0x7EC"``.
    """
    return f"0x{tx_id + 8:03X}"


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


def build_rx_index(ecus: dict | None = None) -> dict[int, str]:
    """Build lookup: RX address (int) -> canonical ECU short name.

    Derived from ``ecus.yaml`` (the superset of all known addresses, including
    non-decodable modules like AMP/SRS), so any responder resolves for display.
    """
    if ecus is None:
        ecus = load_ecus()
    return {tx_id + 8: info["name"] for tx_id, info in ecus.items()}


def ecu_name_from_ref(value, rx_index: dict[int, str] | None = None) -> str:
    """Resolve a capture ``ecu`` reference (RX address) to a canonical name.

    Sentinels and unresolvable references are returned unchanged (stringified),
    so callers always get a printable label.
    """
    if rx_index is None:
        rx_index = build_rx_index()
    rx = parse_ecu_ref(value)
    if rx is not None and rx in rx_index:
        return rx_index[rx]
    return str(value)


class EcuNameCollision(ValueError):
    """Raised when an ECU name or alias resolves ambiguously to >1 ECU.

    Guards the name/alias lookup so an ambiguous registry (two ECUs sharing an
    alias, or an alias equal to a different ECU's name) fails loudly instead of
    silently resolving to the wrong module.
    """


def build_name_tx_index(ecus: dict | None = None) -> dict[str, int]:
    """Build lookup: upper-cased ECU name (and alias) -> TX id.

    Primary ``name`` keys are assigned first (authoritative), then ``alias``
    keys. Raises :class:`EcuNameCollision` if any name/alias is claimed by more
    than one ECU, so aliases can be trusted for lookups without silently masking
    the wrong ECU.
    """
    if ecus is None:
        ecus = load_ecus()
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
                    f"{ecu_name(prev, ecus)} (0x{prev:03X}) and "
                    f"{ecu_name(tx_id, ecus)} (0x{tx_id:03X})"
                )
            index[key] = tx_id
    return index


def build_canonical_name_index(ecus: dict | None = None) -> dict[str, str]:
    """Build lookup: upper-cased ECU name/alias -> canonical short name.

    Both the primary ``name`` and any ``alias`` resolve to the canonical
    ``name``. Reuses :func:`build_name_tx_index`, so it inherits the same
    collision safety (raises :class:`EcuNameCollision` on ambiguity).
    """
    if ecus is None:
        ecus = load_ecus()
    return {
        key: ecu_name(tx_id, ecus)
        for key, tx_id in build_name_tx_index(ecus).items()
    }


def canonical_ecu_name(
    name,
    name_index: dict[str, str] | None = None,
    ecus: dict | None = None,
) -> str:
    """Resolve an ECU name/alias to its canonical short name.

    Accepts a name or alias (case-insensitive). Unknown values are returned
    upper-cased and unchanged, so callers can still match/report them. Raises
    :class:`EcuNameCollision` (via the index) if the registry is ambiguous.
    """
    if name is None:
        return ""
    if name_index is None:
        name_index = build_canonical_name_index(ecus)
    key = str(name).strip().upper()
    return name_index.get(key, key)


def rx_from_name(name: str, name_index: dict[str, int] | None = None) -> str | None:
    """Resolve an ECU name/alias to its RX response-address string, or None."""
    if name_index is None:
        name_index = build_name_tx_index()
    tx_id = name_index.get(str(name).strip().upper())
    return rx_addr_str(tx_id) if tx_id is not None else None


def resolve_tx(value, name_index: dict[str, int] | None = None) -> int | None:
    """Resolve an ECU name/alias or hex TX ID to a TX id int, or None.

    Accepts an ECU name/alias ('BMS', 'igpm') or a hex TX id ('7E4', '0x770').
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if not s:
        return None
    if name_index is None:
        name_index = build_name_tx_index()
    tx_id = name_index.get(s.upper())
    if tx_id is not None:
        return tx_id
    try:
        return int(s.removeprefix("0x").removeprefix("0X"), 16)
    except ValueError:
        return None


def ecu_display(tx_id: int, ecus: dict | None = None) -> str:
    """Human-friendly label for a TX id, e.g. 'BMS (0x7E4)' or '0x7E4'."""
    name = ecu_name(tx_id, ecus)
    return f"0x{tx_id:03X}" if name.startswith("0x") else f"{name} (0x{tx_id:03X})"

