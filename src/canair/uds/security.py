"""SecurityAccess request classification, independent of ECU definitions.

The seed and response may disclose security material even when a profile does not mark a PID
sensitive. They must never be used as identifiers in logs, captures or frame-count ledgers.
"""

from canair.uds.protocol import KWP, DiagProtocol
from canair.uds.response import UdsResponse
from canair.uds.services import NEGATIVE_RESPONSE_SID, SECURITY_ACCESS, positive_response_sid
from canair.uds.subfunctions import SUPPRESS_POSITIVE_RESPONSE


class SecurityAccessError(ValueError):
    """An exchange failed without exposing its sensitive payloads."""


def is_security_request(request: str | bytes) -> bool:
    """Whether a request begins with SecurityAccess, including an ELM count digit."""
    if isinstance(request, bytes):
        return bool(request) and request[0] == SECURITY_ACCESS
    text = "".join(request.split()).upper()
    return text.startswith(f"{SECURITY_ACCESS:02X}")


def is_security_response(response: bytes) -> bool:
    """A positive or negative SecurityAccess reply, even if it arrives late."""
    return bool(response) and (
        response[0] == positive_response_sid(SECURITY_ACCESS)
        or (len(response) > 1 and response[:2] == bytes([NEGATIVE_RESPONSE_SID, SECURITY_ACCESS]))
    )


def withhold_uncertain_response(response: UdsResponse) -> UdsResponse:
    """Hide a late security reply or an unparseable reply after a security exchange."""
    payload = response.get("bytes") or b""
    if not is_security_response(payload) and (
        response.get("ok") or response.get("nrc") is not None
    ):
        safe: UdsResponse = {**response}
        safe["raw"] = "[withheld]"
        return safe
    result: UdsResponse = {
        "raw": "[withheld]",
        "ok": False,
        "error": "response withheld after SecurityAccess",
    }
    if response.get("error_kind"):
        result["error_kind"] = response["error_kind"]
    return result


def is_seed_request(request: bytes, protocol: DiagProtocol) -> bool:
    """Only an unambiguous, bare odd subfunction is a seed request.

    UDS reserves the high bit for suppressing a response; KWP can use it as part of the
    subfunction. No positive response means no usable seed, so suppressed UDS is not accepted.
    """
    if len(request) != 2 or request[0] != SECURITY_ACCESS:
        return False
    sub = request[1]
    if protocol != KWP and sub & SUPPRESS_POSITIVE_RESPONSE:
        return False
    return bool(sub & 1)
