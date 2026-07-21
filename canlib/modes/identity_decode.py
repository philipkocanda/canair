"""Pure decode + protocol-selection helpers for ECU identity.

No device I/O — these functions turn raw payloads/responses and registry hints
into decoded strings and protocol decisions, so they are trivially unit-tested.
The async device orchestration lives in ``identity.py``.
"""

from ..ecus import ecu_id_protocol

# Plausible calendar-year window for date detection. Anything outside this is
# treated as "not a date" so version/build codes aren't rendered as fake dates.
_MIN_YEAR = 1990
_MAX_YEAR = 2099


def _bcd_byte(n: int) -> int | None:
    """Decode a single BCD byte to 0-99, or None if either nibble is > 9."""
    hi, lo = n >> 4, n & 0x0F
    if hi > 9 or lo > 9:
        return None
    return hi * 10 + lo


def _valid_md(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31


def decode_date(stripped: bytes) -> str | None:
    """Decode a manufacture/programming date, or None if not a plausible date.

    Handles the two encodings seen on Hyundai/Kia ECUs:

    * **BCD** (UDS F18x): ``20 17 06 06`` -> ``2017-06-06`` (3-byte ``YY MM DD``
      or 4-byte ``YYYY MM DD``).
    * **Binary** (some KWP2000 1A records): ``07 E0 03 02`` -> year ``0x07E0`` =
      2016, month 3, day 2 -> ``2016-03-02``.

    Returns None for values that don't form a real calendar date (e.g. version
    codes like ``1E090D14``), so the caller can fall back to text/hex.
    """
    if len(stripped) == 4:
        d0, d1, d2, d3 = (_bcd_byte(x) for x in stripped)
        if None not in (d0, d1, d2, d3):
            year = d0 * 100 + d1
            if _MIN_YEAR <= year <= _MAX_YEAR and _valid_md(d2, d3):
                return f"{year:04d}-{d2:02d}-{d3:02d}"
        year = (stripped[0] << 8) | stripped[1]  # binary: uint16 BE year
        if _MIN_YEAR <= year <= _MAX_YEAR and _valid_md(stripped[2], stripped[3]):
            return f"{year:04d}-{stripped[2]:02d}-{stripped[3]:02d}"
        return None
    if len(stripped) == 3:
        d0, d1, d2 = (_bcd_byte(x) for x in stripped)  # BCD YY MM DD
        if None not in (d0, d1, d2) and _valid_md(d1, d2):
            return f"{2000 + d0:04d}-{d1:02d}-{d2:02d}"
        return None
    return None


def decode_identity_payload(payload_bytes: bytes, fmt: str) -> str:
    """Decode an identity payload to a human-readable string.

    ``fmt`` is a hint (``ascii``/``date``/``hex``/``auto``). ``date`` is only
    honored when the bytes form a plausible calendar date; otherwise (and for
    ``ascii``/``auto``) the payload is rendered as text when mostly printable,
    else as hex.
    """
    stripped = payload_bytes.rstrip(b"\xaa\x00\xff").lstrip(b"\x00")

    if not stripped:
        return "(empty)"

    if fmt == "date":
        decoded = decode_date(stripped)
        if decoded:
            return decoded
        # Not a real date — fall through to text/hex rather than fake one.

    printable = sum(1 for b in stripped if 32 <= b < 127)
    if printable >= max(1, len(stripped)) * 0.6:
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in stripped)
        return text.strip() or stripped.hex().upper()

    return stripped.hex().upper()


def resolve_protocol_hint(tx_id: int, requested: str) -> str | None:
    """Resolve the requested/registry protocol to ``"uds"``/``"kwp"``/``None``.

    ``requested`` is the user's ``--protocol`` (``auto``/``uds``/``kwp``).
    Returns ``None`` when it should be auto-probed on the device.
    """
    requested = (requested or "auto").lower()
    if requested in ("uds", "kwp"):
        return requested
    hint = (ecu_id_protocol(tx_id) or "").upper()
    if hint == "UDS":
        return "uds"
    if hint.startswith("KWP"):
        return "kwp"
    return None  # "none"/"unknown"/missing -> probe


def service_supported(resp: dict) -> bool | None:
    """Interpret a probe response: True=supported, False=not, None=no signal.

    A positive response or any NRC other than serviceNotSupported (0x11) /
    serviceNotSupportedInActiveSession (0x7F) means the service exists. A bare
    ``NO DATA``/timeout carries no signal (ECU asleep or busy).
    """
    if resp.get("ok"):
        return True
    nrc = resp.get("nrc")
    if nrc is not None:
        return nrc not in (0x11, 0x7F)
    return None
