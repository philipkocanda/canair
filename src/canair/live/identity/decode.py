"""Pure decode + protocol-selection helpers for ECU identity.

No device I/O — these functions turn raw payloads/responses and registry hints
into decoded strings and protocol decisions, so they are trivially unit-tested.
The async device orchestration lives in :mod:`.read`.

The date/BCD/ASCII primitives are the shared typed-decode home in
``canair.expression.decode_value`` (so identity DIDs and the analysis suite decode the same
way); this module re-exports ``decode_date`` and layers the identity-specific
``fmt`` selection on top.
"""

from typing import Literal

from canair.expression.decode_value import bytes_to_ascii, decode_date
from canair.profile.model import Profile
from canair.uds.nrc import (
    NOT_IN_ACTIVE_SESSION_NRCS,
    NRC_SERVICE_NOT_SUPPORTED,
    NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION,
)
from canair.uds.protocol import KWP, UDS, UNKNOWN, DiagProtocol
from canair.uds.response import UdsResponse

from ...ecus.protocol import ecu_protocol

__all__ = [
    "decide_protocol",
    "decode_date",
    "decode_identity_payload",
    "probe_state",
    "resolve_protocol_hint",
    "service_supported",
]

# NRCs that refuse the service itself rather than this one request.
_SERVICE_REFUSALS = frozenset({NRC_SERVICE_NOT_SUPPORTED, *NOT_IN_ACTIVE_SESSION_NRCS})
# The refusals that hold only in the active session: the service may exist in another one.
_SESSION_REFUSALS = frozenset(
    {*NOT_IN_ACTIVE_SESSION_NRCS, NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION}
)


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


def resolve_protocol_hint(tx_id: int, requested: str, *, profile: Profile) -> str | None:
    """Resolve the requested/registry protocol to ``"uds"``/``"kwp"``/``None``.

    ``requested`` is the user's ``--protocol`` (``auto``/``uds``/``kwp``).
    Returns ``None`` when it should be auto-probed on the device.
    """
    requested = (requested or "auto").lower()
    if requested in (UDS, KWP):
        return requested
    hint = ecu_protocol(tx_id, profile=profile)
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


ProbeState = Literal["positive", "supported", "session", "refused", "silent"]


def probe_state(resp: UdsResponse) -> ProbeState:
    """How strongly a probe reply shows its service exists.

    ``positive`` (a positive reply) is proof; ``supported`` is an NRC that does not refuse the
    service itself (see :func:`service_supported`), which some ECUs send for a service they
    implement for another protocol; ``session`` refuses it in the active session only
    (``0x7E``, ``0x7F``, ``0x80``), so the service may exist; ``refused`` is
    serviceNotSupported (``0x11``), which holds in every session; ``silent`` is no answer.
    """
    if resp.get("ok"):
        return "positive"
    nrc = resp.get("nrc")
    if nrc is None:
        return "silent"
    if nrc == NRC_SERVICE_NOT_SUPPORTED:
        return "refused"
    return "session" if nrc in _SESSION_REFUSALS else "supported"


def decide_protocol(uds: ProbeState, kwp: ProbeState) -> tuple[DiagProtocol | None, str]:
    """The protocol two probes (``22 F190`` and ``1A 90``) point to, and why.

    A positive reply outweighs an NRC: a KWP2000 ECU that also implements 0x22
    (ReadDataByCommonIdentifier) can answer ``22 F190`` with requestOutOfRange, which
    alone would read as UDS. Two positive replies mean KWP2000, because 0x1A exists only
    there. With no positive reply the first service that is not refused wins, UDS first.
    """
    if kwp == "positive":
        if uds == "positive":
            return KWP, "both probes answered; 1A exists only in KWP2000 (probes 22F190, 1A90)"
        return KWP, "KWP2000 service 1A answered (probe 1A90)"
    if uds == "positive":
        return UDS, "UDS service 22 answered (probe 22F190)"
    if uds == "supported":
        return UDS, "UDS service 22 supported (probe 22F190)"
    if kwp == "supported":
        return KWP, "KWP2000 service 1A supported (probe 1A90)"
    if uds in ("refused", "session") and kwp in ("refused", "session"):
        return None, (
            "neither UDS 22 nor KWP2000 1A supported (NRC 0x11, or 0x7F/0x80 "
            "refused in the active session)"
        )
    return None, "no response to probe (22F190 / 1A90) — ECU may be asleep or unpowered"
