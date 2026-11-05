"""Pure decode + protocol-selection helpers for ECU identity.

No device I/O — these functions turn raw payloads/responses and registry hints
into decoded strings and protocol decisions, so they are trivially unit-tested.
The async device orchestration lives in ``identity.py``.

The date/BCD/ASCII primitives are the shared typed-decode home in
``canair.expression.decode_value`` (so identity DIDs and the analysis suite decode the same
way); this module re-exports ``decode_date`` and layers the identity-specific
``fmt`` selection on top.
"""

from canair.expression.decode_value import bytes_to_ascii, decode_date
from canair.uds.nrc import NOT_IN_ACTIVE_SESSION_NRCS, NRC_SERVICE_NOT_SUPPORTED
from canair.uds.protocol import KWP, UDS, UNKNOWN
from canair.uds.response import UdsResponse

from ...ecus.protocol import ecu_protocol

__all__ = [
    "decode_date",
    "decode_identity_payload",
    "resolve_protocol_hint",
    "service_supported",
]

# NRCs that refuse the service itself rather than this one request.
_SERVICE_REFUSALS = frozenset({NRC_SERVICE_NOT_SUPPORTED, *NOT_IN_ACTIVE_SESSION_NRCS})


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
    if requested in (UDS, KWP):
        return requested
    hint = ecu_protocol(tx_id)
    return None if hint == UNKNOWN else hint  # "none"/"unknown"/missing -> probe


def service_supported(resp: UdsResponse) -> bool | None:
    """Interpret a probe response: True=supported, False=not, None=no signal.

    A positive response or any NRC other than the refusals of the service itself —
    serviceNotSupported (0x11), and not-in-the-active-session as UDS (0x7F) and
    KWP2000 (0x80) spell it — means the service exists. A bare ``NO DATA``/timeout
    carries no signal (ECU asleep or busy).
    """
    if resp.get("ok"):
        return True
    nrc = resp.get("nrc")
    if nrc is not None:
        return nrc not in _SERVICE_REFUSALS
    return None
