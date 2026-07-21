"""Pure decode + protocol-selection helpers for ECU identity.

No device I/O — these functions turn raw payloads/responses and registry hints
into decoded strings and protocol decisions, so they are trivially unit-tested.
The async device orchestration lives in ``identity.py``.
"""

from ..ecus import ecu_id_protocol


def decode_identity_payload(payload_bytes: bytes, fmt: str) -> str:
    """Decode an identity payload to a human-readable string.

    ``fmt`` is a hint (``ascii``/``date``/``hex``/``auto``); ASCII and auto both
    fall back to hex when the bytes are not mostly printable text.
    """
    stripped = payload_bytes.rstrip(b"\xaa\x00\xff").lstrip(b"\x00")

    if not stripped:
        return "(empty)"

    if fmt == "date" and len(stripped) >= 3:
        hex_str = stripped.hex().upper()
        if len(hex_str) == 8:
            return f"{hex_str[0:4]}-{hex_str[4:6]}-{hex_str[6:8]}"
        if len(hex_str) == 6:
            return f"20{hex_str[0:2]}-{hex_str[2:4]}-{hex_str[4:6]}"
        return hex_str

    if fmt in ("ascii", "auto"):
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
