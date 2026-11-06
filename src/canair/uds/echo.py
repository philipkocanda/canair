"""What a positive reply must repeat of its request — the stale/misfiled-frame check.

UDS carries no transaction id, so the only per-reply proof that a response answers
*this* request is its echo: the request SID ``+0x40`` followed by the request's
identifier bytes. :func:`request_echo` derives what to expect from a request and
:func:`echo_mismatch` compares a reply against it; the live parser
(:func:`canair.uds.parse.parse_uds_response`) and the stored-payload lint
(:func:`canair.uds.payload.payload_echo_mismatch`) both judge through it, so the
Hyundai/Kia identity tolerance and the comparison itself live once.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from canair.uds.memory import parse_memory_request
from canair.uds.protocol import UDS, DiagProtocol
from canair.uds.services import (
    ACCESS_TIMING_PARAMETER,
    COMMUNICATION_CONTROL,
    CONTROL_DTC_SETTING,
    DIAGNOSTIC_SESSION_CONTROL,
    DYNAMICALLY_DEFINE_DATA_IDENTIFIER,
    ECU_RESET,
    LINK_CONTROL,
    READ_DATA_BY_IDENTIFIER,
    READ_DTC_INFORMATION,
    READ_MEMORY_BY_ADDRESS,
    RESPONSE_ON_EVENT,
    RESPONSE_SID_OFFSET,
    ROUTINE_CONTROL,
    SECURITY_ACCESS,
    TESTER_PRESENT,
    identifier_width,
)
from canair.uds.subfunctions import SUPPRESS_POSITIVE_RESPONSE

# UDS services whose positive reply repeats the request's sub-function right after its SID
# (ISO 14229-1), without the suppress-positive-response bit. RoutineControl repeats its
# routine id too. Judged only on an ECU known to speak UDS: some KWP2000 ECUs do not repeat
# a session type, KWP2000's TesterPresent reply may carry nothing after its SID, and
# KWP2000's 0x31 is StartRoutineByLocalIdentifier, with a one-byte local identifier.
_SUBFUNCTION_ECHO_SERVICES = frozenset(
    {
        DIAGNOSTIC_SESSION_CONTROL,
        ECU_RESET,
        READ_DTC_INFORMATION,
        SECURITY_ACCESS,
        COMMUNICATION_CONTROL,
        TESTER_PRESENT,
        CONTROL_DTC_SETTING,
    }
)


# UDS services whose sub-function carries the suppressPosRspMsgIndicationBit (ISO 14229-1).
# ReadDTCInformation is not one: its reply is the point of the request.
_SUPPRESSIBLE_SERVICES = _SUBFUNCTION_ECHO_SERVICES - {READ_DTC_INFORMATION} | {
    ROUTINE_CONTROL,
    DYNAMICALLY_DEFINE_DATA_IDENTIFIER,
    ACCESS_TIMING_PARAMETER,
    RESPONSE_ON_EVENT,
    LINK_CONTROL,
}


def positive_response_suppressed(request_hex: str | None, protocol: DiagProtocol | None) -> bool:
    """Whether ``request_hex`` asks a UDS ECU to send no positive reply (sub-function bit 7).

    Only on an ECU known to speak UDS: on KWP2000 ``10 81`` is a session that answers, and
    for an unknown protocol the request may be either. An odd length is an ELM327 request
    plus its expected-frame-count digit, which is ignored.
    """
    if protocol != UDS or not request_hex:
        return False
    text = "".join(request_hex.split())
    req = hex_bytes(text[:-1] if len(text) % 2 else text)
    return (
        req is not None
        and len(req) >= 2
        and req[0] in _SUPPRESSIBLE_SERVICES
        and bool(req[1] & SUPPRESS_POSITIVE_RESPONSE)
    )


def hex_bytes(text: str) -> bytes | None:
    """Bytes of an even-length hex string (spaces ignored), or None if it is not one."""
    cleaned = text.replace(" ", "").strip()
    if len(cleaned) < 2 or len(cleaned) % 2 != 0:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


def request_echo(
    request_hex: str, protocol: DiagProtocol | None = None
) -> tuple[int, bytes] | None:
    """Derive the (expected_sid, echo_bytes) a positive response must repeat.

    Which request bytes count as the identifier is service-specific:

    - ``1A xx``   (KWP2000 readEcuIdentification): 1-byte identificationOption echo —
      ``5A xx …``, as ISO 14230-3 specifies and every Ioniq KWP2000 ECU answers.
    - ``21 xx``   (readDataByLocalId / mfr live data): 1-byte PID echo — the
      response is ``61 xx …``. This is the case the ELM327 stale-frame bug hits
      (a ``61 01`` response leaking into a ``2102`` request slot passes a
      SID-only check because both PIDs share SID ``0x61``).
    - ``22 xxxx`` (readDataByIdentifier): 2-byte DID echo — ``62 xx xx …``. A
      multi-DID batch (``22 xxxx yyyy …``) is validated against its *first* DID,
      which the response must still repeat immediately after the SID. A batch's
      later DIDs sit at data-dependent offsets, so they are left to
      :func:`canair.live.read.batch.split_multi_did`, which walks the response.
    - ``01 xx`` / ``09 xx`` (OBD-II current data / vehicle information): 1-byte PID
      or InfoType echo — ``41 xx …`` / ``49 xx …``. Only the single-PID form: a
      multi-PID ``01 0C 0D …`` reply interleaves each PID with its data, so it is
      left unvalidated.
    - ``23 …`` (ReadMemoryByAddress): no echo at all, only the SID — ``63 data``. Its
      length is the check instead (:func:`reply_check`).
    - With ``protocol`` ``UDS``, a sub-function service (``10``, ``11``, ``19``, ``27``,
      ``28``, ``3E``, ``85``): the sub-function without its suppress-positive-response
      bit — ``50 03 …`` — and for ``31`` the routine id too — ``71 01 F0 00 …``.

    Returns ``(sid, echo_bytes)`` for those services, or ``None`` when the request
    isn't a plain identifier read we can validate (unknown length, sub-function
    services, etc.) — callers should then skip echo validation.
    """
    check = reply_check(request_hex, protocol)
    return None if check is None else (check.sid, check.echo)


@dataclass(frozen=True)
class ReplyCheck:
    """What a positive reply to a request must carry.

    ``sid`` is the request's service, ``echo`` the identifier (or sub-function) bytes
    the reply repeats after its SID, and ``length`` the reply's exact length when the request fixes it
    (a memory read), else ``None``.
    """

    sid: int
    echo: bytes
    length: int | None = None


@lru_cache(maxsize=4096)
def reply_check(request_hex: str, protocol: DiagProtocol | None = None) -> ReplyCheck | None:
    """The :class:`ReplyCheck` for a request, or ``None`` when its reply cannot be judged.

    ``protocol`` decides a memory read's request form
    (:func:`canair.uds.memory.parse_memory_request`), and only on ``UDS`` is a
    sub-function service judged: its reply must repeat the sub-function (and, for
    RoutineControl, the routine id). Without it those services cannot be judged.

    Cached: a capture store repeats a few hundred requests across hundreds of thousands
    of replies, and the result is a frozen value of its arguments alone.
    """
    req = hex_bytes(request_hex)
    if req is None:
        return None
    sid = req[0]
    if sid == READ_MEMORY_BY_ADDRESS:
        read = parse_memory_request(req.hex(), protocol)
        return ReplyCheck(sid, b"", read.reply_length if read is not None else None)
    if identifier_width(sid) == 1:
        return ReplyCheck(sid, req[1:2]) if len(req) == 2 else None
    if sid == READ_DATA_BY_IDENTIFIER:
        odd_length = len(req) % 2 == 1  # SID + whole 2-byte DIDs
        return ReplyCheck(sid, req[1:3]) if len(req) >= 3 and odd_length else None
    if protocol == UDS and len(req) >= 2:
        sub = bytes([req[1] & 0x7F])  # without the suppress-positive-response bit
        if sid == ROUTINE_CONTROL:
            return ReplyCheck(sid, sub + req[2:4]) if len(req) >= 4 else None
        if sid in _SUBFUNCTION_ECHO_SERVICES:
            return ReplyCheck(sid, sub)
    return None


@dataclass(frozen=True)
class EchoMismatch:
    """How a positive reply fails to echo its request.

    ``field`` is ``sid`` (wrong response SID — another service's reply), ``length``
    (too short to hold the identifier) or ``id`` (another identifier's reply).
    ``expected``/``got`` are the bytes compared (for ``length``, ``got`` is all the
    reply had after its SID).
    """

    field: Literal["sid", "length", "id"]
    expected: bytes
    got: bytes


def echo_mismatch(expected_sid: int, echo: bytes, response: bytes) -> EchoMismatch | None:
    """Compare a non-negative ``response`` against the echo its request expects."""
    expected_resp_sid = (expected_sid + RESPONSE_SID_OFFSET) & 0xFF
    if not response or response[0] != expected_resp_sid:
        return EchoMismatch("sid", bytes([expected_resp_sid]), response[:1])
    if not echo:
        return None
    got = response[1 : 1 + len(echo)]
    if len(got) < len(echo):
        return EchoMismatch("length", echo, got)
    if got != echo:
        return EchoMismatch("id", echo, got)
    return None
