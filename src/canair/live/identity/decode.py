"""Pure decode + protocol-selection helpers for ECU identity.

No device I/O — these functions turn raw payloads/responses and registry hints
into decoded strings and protocol decisions, so they are trivially unit-tested.
The async device orchestration lives in :mod:`.read`.

The date/BCD/ASCII primitives are the shared typed-decode home in
``canair.expression.decode_value`` (so identity DIDs and the analysis suite decode the same
way); this module re-exports ``decode_date`` and layers the identity-specific
``fmt`` selection on top.
"""

from collections.abc import Mapping
from typing import Literal

from canair.expression.decode_value import bytes_to_ascii, decode_date
from canair.profile.model import Profile
from canair.uds.nrc import (
    KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION,
    NOT_IN_ACTIVE_SESSION_NRCS,
    NRC_SECURITY_ACCESS_DENIED,
    NRC_SERVICE_NOT_SUPPORTED,
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
    NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION,
)
from canair.uds.protocol import KWP, UDS, UNKNOWN, DiagProtocol
from canair.uds.response import UdsResponse

from ...ecus.protocol import ecu_protocol

__all__ = [
    "SESSION_NRCS",
    "decide_protocol",
    "decode_date",
    "decode_identity_payload",
    "probe_state",
    "refusal_hint",
    "resolve_protocol_hint",
    "service_supported",
]

# NRCs that refuse the service itself rather than this one request.
_SERVICE_REFUSALS = frozenset({NRC_SERVICE_NOT_SUPPORTED, *NOT_IN_ACTIVE_SESSION_NRCS})
# The refusals that hold only in the active session: the service may exist in another one.
SESSION_NRCS = frozenset(
    {*NOT_IN_ACTIVE_SESSION_NRCS, NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION}
)
# 0x7E and 0x7F exist only in UDS (ISO 14229-1). 0x80 is KWP2000's spelling; UDS does not define it.
_UDS_SESSION_NRCS = frozenset(
    {NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION, NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION}
)
_KWP_SESSION_NRC = KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION


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
    return "session" if nrc in SESSION_NRCS else "supported"


def decide_protocol(
    uds: ProbeState,
    kwp: ProbeState,
    uds_nrc: int | None = None,
    kwp_nrc: int | None = None,
) -> tuple[DiagProtocol | None, str]:
    """The protocol two probes (``22 F190`` and ``1A 90``) point to, and why.

    A positive reply outweighs an NRC: a KWP2000 ECU that also implements 0x22
    (ReadDataByCommonIdentifier) can answer ``22 F190`` with requestOutOfRange, which
    alone would read as UDS. Two positive replies mean KWP2000, because 0x1A exists only
    there. With no positive reply the first service that is not refused wins, UDS first.

    A refusal for the active session names its protocol when the NRC is given: ``0x7E``/``0x7F``
    exist only in UDS, ``0x80`` only in KWP2000. So ``22 F190`` refused with ``0x7F`` says the
    ECU has service 0x22 in another session. If both probes say so, nothing is decided.
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
    uds_in_session = uds == "session" and uds_nrc in _UDS_SESSION_NRCS
    kwp_in_session = kwp == "session" and kwp_nrc == _KWP_SESSION_NRC
    if uds_in_session != kwp_in_session:
        if uds_in_session:
            return UDS, f"UDS service 22 exists but is refused in this session (0x{uds_nrc:02X})"
        return KWP, f"KWP2000 service 1A exists but is refused in this session (0x{kwp_nrc:02X})"
    if uds in ("refused", "session") and kwp in ("refused", "session"):
        return None, (
            "neither UDS 22 nor KWP2000 1A supported (NRC 0x11, or 0x7F/0x80 "
            "refused in the active session)"
        )
    return None, "no response to probe (22F190 / 1A90) — ECU may be asleep or unpowered"


def refusal_hint(
    codes: Mapping[int, int], n_ok: int, *, session: bool, protocol: str | None
) -> str | None:
    """The hint that fits the NRCs a reading drew, or None.

    ``codes`` counts the NRCs by code. With no field read, the hint follows the code most
    records drew: a session refusal points at ``--session`` (or, with ``--session`` given, at
    the session canair opened), ``0x33`` at security access, ``0x31``/``0x12``/``0x11`` says
    the ECU has none of the standard records. With some fields read, session refusals alone
    point at ``--session``.
    """
    refused_in_session = sum(n for code, n in codes.items() if code in SESSION_NRCS)
    if n_ok:
        if refused_in_session and not session:
            return "--session may return more fields"
        return None
    total = sum(codes.values())
    if not total:
        return None
    if refused_in_session * 2 > total:
        if not session:
            return "these records need a diagnostic session: try --session"
        text = "the ECU refused these records in the session canair opened (10 03)"
        if protocol == KWP:
            text += ".\nA KWP2000 ECU may need session 10 81, which canair identity cannot open yet"
        return text
    if codes.get(NRC_SECURITY_ACCESS_DENIED, 0) * 2 > total:
        return "these records need security access, which canair does not unlock"
    absent = sum(codes.get(code, 0) for code in (0x31, 0x12, 0x11))
    if absent * 2 > total:
        return "this ECU answers none of the standard identity records"
    return None
