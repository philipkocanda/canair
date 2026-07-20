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
        data = yaml.safe_load(f)
    result = {}
    for tx_id, info in data.get("ecus", {}).items():
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


def build_name_tx_index(ecus: dict | None = None) -> dict[str, int]:
    """Build lookup: upper-cased ECU name (and alias) -> TX id."""
    if ecus is None:
        ecus = load_ecus()
    index: dict[str, int] = {}
    for tx_id, info in ecus.items():
        name = info.get("name")
        if name:
            index[str(name).upper()] = tx_id
        alias = info.get("alias")
        if alias:
            index.setdefault(str(alias).upper(), tx_id)
    return index


def rx_from_name(name: str, name_index: dict[str, int] | None = None) -> str | None:
    """Resolve an ECU name/alias to its RX response-address string, or None."""
    if name_index is None:
        name_index = build_name_tx_index()
    tx_id = name_index.get(str(name).strip().upper())
    return rx_addr_str(tx_id) if tx_id is not None else None

