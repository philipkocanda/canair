"""Pure decode + protocol-selection helpers for ECU identity.

No device I/O — these functions turn raw payloads/responses and registry hints
into decoded strings and protocol decisions, so they are trivially unit-tested.
The async device orchestration lives in ``identity.py``.

The date/BCD/ASCII primitives are the shared typed-decode home in
``canlib.decode_value`` (so identity DIDs and the analysis suite decode the same
way); this module re-exports ``decode_date`` and layers the identity-specific
``fmt`` selection on top.
"""

from ..decode_value import bytes_to_ascii, decode_date
from ..ecus import ecu_id_protocol

__all__ = [
    "decode_date",
    "decode_identity_payload",
    "resolve_protocol_hint",
    "service_supported",
]


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

    return bytes_to_ascii(stripped)


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
