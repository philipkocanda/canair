"""SecurityAccess request classification, independent of ECU definitions.

The seed and response may disclose security material even when a profile does not mark a PID
sensitive. They must never be used as identifiers in logs, captures or frame-count ledgers.
"""

from canair.uds.parse import ADAPTER_STATUS
from canair.uds.protocol import KWP, DiagProtocol
from canair.uds.response import CAT_BUS, CAT_NO_DATA, UdsResponse
from canair.uds.services import NEGATIVE_RESPONSE_SID, SECURITY_ACCESS, positive_response_sid
from canair.uds.subfunctions import SUPPRESS_POSITIVE_RESPONSE

# Failures whose error text is fixed adapter or transport wording, never a reply's bytes.
_PLAIN_FAILURES = frozenset({CAT_NO_DATA, CAT_BUS})
WITHHELD_ERROR = "response withheld after SecurityAccess"
WITHHELD_NOTE = "  SecurityAccess response withheld"

# What an adapter answers to an AT command or when it has no reply: no ECU data in it.
_ADAPTER_REPLIES = frozenset(ADAPTER_STATUS) | {"OK"}


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
    kind = response.get("error_kind")
    plain = kind in _PLAIN_FAILURES and response.get("error")
    result: UdsResponse = {
        "raw": "[withheld]",
        "ok": False,
        "error": plain or WITHHELD_ERROR,
    }
    if kind:
        result["error_kind"] = kind
    return result


def displayed_reply(
    cmd: str, raw: str, response: UdsResponse, *, security_seen: bool
) -> tuple[str, bool]:
    """The text a REPL prints for a typed command's reply, and whether it was withheld.

    A SecurityAccess request or reply is always withheld. Once the connection has carried
    one, a late reply may still arrive, so only adapter status lines and parsed payloads are
    shown as sent; any other text is not echoed.
    """
    if is_security_request(cmd) or is_security_response(response.get("bytes") or b""):
        return WITHHELD_NOTE, True
    if not security_seen or raw.strip().upper() in _ADAPTER_REPLIES:
        return raw, False
    if response.get("ok") or response.get("nrc") is not None:
        return response.get("hex", "[reply withheld]"), False
    return "  [reply withheld]", True


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
