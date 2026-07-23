"""UDS response parsing and Negative Response Code (NRC) tables.

Transport-independent: both the ``wican-ws`` (ELM327 dongle) and ``slcan-tcp``
(client-side ISO-TP) paths funnel their raw response text/hex through
:func:`parse_uds_response` so downstream code sees an identical result dict
regardless of which transport produced the bytes. The parser tolerates
ELM327-flavored artifacts (AT echoes, ``NO DATA``/``CAN ERROR`` strings, ``>``
prompts, flow-control frame echoes) which never appear on the raw path but are
harmless there.
"""

import re

# UDS Negative Response Code descriptions
NRC_CODES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

# Short mnemonics for compact UI display (TUI "Last response" column, etc).
# Derived from the initials of each NRC name; use `nrc_abbrev(n)` for lookup.
NRC_ABBREV = {
    0x10: "GR",  # generalReject
    0x11: "SNS",  # serviceNotSupported
    0x12: "SFNS",  # subFunctionNotSupported
    0x13: "IMLIF",  # incorrectMessageLengthOrInvalidFormat
    0x14: "RTL",  # responseTooLong
    0x21: "BRR",  # busyRepeatRequest
    0x22: "CNC",  # conditionsNotCorrect
    0x24: "RSE",  # requestSequenceError
    0x25: "NRFSC",  # noResponseFromSubnetComponent
    0x26: "FPE",  # failurePreventsExecution
    0x31: "ROOR",  # requestOutOfRange
    0x33: "SAD",  # securityAccessDenied
    0x35: "IK",  # invalidKey
    0x36: "ENOA",  # exceededNumberOfAttempts
    0x37: "RTDNE",  # requiredTimeDelayNotExpired
    0x70: "UDNA",  # uploadDownloadNotAccepted
    0x71: "TDS",  # transferDataSuspended
    0x72: "GPF",  # generalProgrammingFailure
    0x73: "WBSC",  # wrongBlockSequenceCounter
    0x78: "RCRRP",  # requestCorrectlyReceivedResponsePending
    0x7E: "SFNSIAS",  # subFunctionNotSupportedInActiveSession
    0x7F: "SNSIAS",  # serviceNotSupportedInActiveSession
}


def nrc_abbrev(nrc: int) -> str:
    """Short mnemonic for an NRC, or ``?`` if unknown."""
    return NRC_ABBREV.get(nrc, "?")


def request_echo(request_hex: str) -> tuple[int, bytes] | None:
    """Derive the (expected_sid, echo_bytes) a positive response must repeat.

    A UDS positive response echoes the request SID (``+0x40``) followed by the
    request's *identifier* bytes verbatim. Which bytes count as the identifier
    is service-specific:

    - ``21 xx``   (readDataByLocalId / mfr live data): 1-byte PID echo — the
      response is ``61 xx …``. This is the case the ELM327 stale-frame bug hits
      (a ``61 01`` response leaking into a ``2102`` request slot passes a
      SID-only check because both PIDs share SID ``0x61``).
    - ``22 xxxx`` (readDataByIdentifier): 2-byte DID echo — ``62 xx xx …``.

    Returns ``(sid, echo_bytes)`` for those two services (``echo_bytes`` may be
    empty if the request carried no identifier), or ``None`` when the request
    isn't a plain identifier read we can validate (unknown length, sub-function
    services, etc.) — callers should then skip echo validation.
    """
    cleaned = request_hex.replace(" ", "").strip()
    if len(cleaned) < 2 or len(cleaned) % 2 != 0:
        return None
    try:
        req = bytes.fromhex(cleaned)
    except ValueError:
        return None
    sid = req[0]
    if sid == 0x21:
        # service 21 carries a 1-byte PID (a multi-DID 21 request is unusual;
        # only validate the single-PID form we actually emit).
        return (sid, req[1:2]) if len(req) == 2 else None
    if sid == 0x22:
        # service 22 carries one 2-byte DID (multi-DID batches skip validation).
        return (sid, req[1:3]) if len(req) == 3 else None
    return None


def _hk_identity_offset(expected_sid: int, expected_id: bytes, got: bytes) -> bool:
    """True when ``got`` is the expected identifier minus one — the Hyundai/Kia
    identity-DID quirk (request 22F188 -> response 62F187). Expected ECU
    behaviour on HK modules, not a stale/misfiled frame, so echo validation
    tolerates it for F1xx DIDs.
    """
    return (
        expected_sid == 0x22
        and len(expected_id) == 2
        and len(got) == 2
        and expected_id[0] == 0xF1
        and got[0] == 0xF1
        and got[1] == (expected_id[1] - 1) & 0xFF
    )


def payload_echo_mismatch(request_pid: str, payload_hex: str) -> str | None:
    """Cross-check a stored capture payload against the PID it was recorded for.

    Returns a human-readable reason string when the payload's SID+identifier
    echo does NOT match ``request_pid`` (e.g. a ``6101…`` payload filed under a
    ``2102`` request — the ELM327 stale-frame bug), or ``None`` when it matches
    or when we can't validate (non-identifier request, NRC/short payload, hex we
    can't parse). Only known echo mismatches are reported, so this is safe to
    surface as a soft lint warning.
    """
    echo = request_echo(request_pid)
    if echo is None:
        return None  # not a plain 21xx/22xxxx read — nothing to check
    expected_sid, expected_id = echo
    cleaned = payload_hex.replace(" ", "").strip()
    if len(cleaned) < 2 or len(cleaned) % 2 != 0:
        return None
    try:
        resp = bytes.fromhex(cleaned)
    except ValueError:
        return None
    if resp[0] == 0x7F:
        return None  # negative response — not an echo, leave to other checks
    expected_resp_sid = (expected_sid + 0x40) & 0xFF
    if resp[0] != expected_resp_sid:
        return (
            f"payload SID 0x{resp[0]:02X} != expected 0x{expected_resp_sid:02X} "
            f"for request {request_pid}"
        )
    if expected_id and resp[1 : 1 + len(expected_id)] != expected_id:
        got = resp[1 : 1 + len(expected_id)]
        # Hyundai/Kia identity DIDs answer one *less* than requested (the HK -1
        # offset: 22F188 -> 62F187, etc.). That's expected ECU behaviour, not a
        # misfiled frame, so don't flag F1xx reads that are exactly off-by-one.
        if _hk_identity_offset(expected_sid, expected_id, got):
            return None
        return (
            f"payload echoes id 0x{got.hex().upper()} but request {request_pid} "
            f"expects 0x{expected_id.hex().upper()} (stale/misfiled frame?)"
        )
    return None


def parse_uds_response(
    raw: str,
    expected_sid: int | None = None,
    expected_did: int | None = None,
    expected_echo: bytes | None = None,
) -> dict:
    """Parse a UDS response (as returned by any transport) into structured data.

    Args:
        raw: Raw response text. On the ``wican-ws`` path this is ELM327
            terminal output; on the ``slcan-tcp`` path :class:`RawTerminal`
            formats the reassembled ISO-TP payload into the same shape.
        expected_sid: If set, the positive response must echo this request SID
            (i.e. response byte 0 == expected_sid + 0x40). Mismatches are
            reported as ``error="SID mismatch: ..."`` and ``ok=False``. Used
            to catch stale/misaligned responses from the ELM327 adapter
            where a late-arriving frame from a previous request leaks into
            the next read (seen during 0x2F IOControl scans — see
            ``canlib/modes/iocontrol_scan.py``).
        expected_did: If set AND ``expected_sid`` is set, the positive
            response must also echo this 16-bit DID in bytes 1..2
            (big-endian). Used by services that carry a DID
            immediately after the SID: 0x22 ReadDataByIdentifier,
            0x2E WriteDataByIdentifier, 0x2F InputOutputControlByIdentifier.
        expected_echo: If set AND ``expected_sid`` is set, the positive response
            must echo these identifier bytes verbatim starting at byte 1. This
            is the variable-width generalization of ``expected_did`` — a 1-byte
            value validates the service-21 PID echo (catching a ``6101`` frame
            returned for a ``2102`` request), a 2-byte value the service-22 DID.
            Prefer :func:`request_echo` to derive it from the request. If both
            ``expected_did`` and ``expected_echo`` are given, ``expected_echo``
            takes precedence.

    Returns dict with keys:
        ok: bool - whether a positive and (if requested) echo-matching response was received
        hex: str - raw hex string of response data (if ok)
        bytes: bytes - parsed response bytes (if ok)
        nrc: int - negative response code (if not ok)
        nrc_desc: str - NRC description (if not ok)
        error: str - error message (if parse failed or echo mismatched)
        raw: str - original response text
    """
    result = {"raw": raw, "ok": False}

    lines = raw.strip().split("\n")
    lines = [line.strip() for line in lines if line.strip()]

    data_lines = []
    for line in lines:
        line = line.rstrip(">").strip()
        if not line:
            continue
        if line.startswith("AT") or line.startswith("at"):
            continue
        if line == "OK":
            continue
        # Filter out ISO-TP flow control frame echoes
        fc_check = line.replace(" ", "").upper()
        if len(fc_check) >= 6 and fc_check[:3].isalnum():
            fc_body = fc_check[3:]
        else:
            fc_body = fc_check
        if fc_body.startswith("F0") and len(fc_body) <= 8:
            continue
        if line == "?":
            result["error"] = "Unknown command"
            return result
        if line == "NO DATA":
            result["error"] = "No response from ECU (NO DATA)"
            return result
        if line == "CAN ERROR":
            result["error"] = "CAN bus error"
            return result
        if line == "UNABLE TO CONNECT":
            result["error"] = "Unable to connect to CAN bus"
            return result
        if line == "BUS INIT: ...ERROR":
            result["error"] = "Bus initialization error"
            return result
        if line == "STOPPED":
            result["error"] = "Request stopped"
            return result
        if line == "BUFFER FULL":
            result["error"] = "Response buffer full"
            return result
        data_lines.append(line)

    if not data_lines:
        result["error"] = "Empty response"
        return result

    # Filter out echo of the request
    if len(data_lines) > 1:
        first = data_lines[0].replace(" ", "")
        if len(first) >= 2 and all(c in "0123456789ABCDEFabcdef" for c in first):
            first_byte = int(first[:2], 16)
            if 0x10 <= first_byte <= 0x3E:
                data_lines = data_lines[1:]

    # Check for multi-frame ISO-TP format
    is_multiframe = any(re.match(r"^\d+:", line) for line in data_lines)

    if is_multiframe:
        frame_lines = []
        for line in data_lines:
            m = re.match(r"^(\d+):([0-9A-Fa-f]+)$", line)
            if m:
                frame_lines.append((int(m.group(1)), m.group(2)))
        frame_lines.sort(key=lambda x: x[0])
        hex_clean = "".join(hex_data for _, hex_data in frame_lines)
    else:
        hex_str = " ".join(data_lines)
        hex_clean = hex_str.replace(" ", "")

    if not all(c in "0123456789ABCDEFabcdef" for c in hex_clean):
        result["error"] = f"Non-hex response: {hex_clean[:80]}"
        return result

    if len(hex_clean) < 2:
        result["error"] = f"Response too short: {hex_clean}"
        return result

    try:
        response_bytes = bytes.fromhex(hex_clean)
    except ValueError as e:
        result["error"] = f"Hex decode failed: {e}"
        return result

    result["hex"] = hex_clean.upper()
    result["bytes"] = response_bytes

    if response_bytes[0] == 0x7F and len(response_bytes) >= 3:
        nrc = response_bytes[2]
        result["nrc"] = nrc
        result["nrc_service"] = response_bytes[1]
        result["nrc_desc"] = NRC_CODES.get(nrc, f"unknown (0x{nrc:02X})")
        if expected_sid is not None and response_bytes[1] != expected_sid:
            # NRC is reporting rejection for a *different* service — this is
            # a stale/misaligned frame, not a real NRC for our request.
            result["error"] = (
                f"NRC echo mismatch: NRC service byte 0x{response_bytes[1]:02X} "
                f"!= expected SID 0x{expected_sid:02X}"
            )
            # Keep nrc/nrc_desc for diagnostics, but leave ok=False.
            result.pop("nrc", None)
            result.pop("nrc_service", None)
            result.pop("nrc_desc", None)
        return result

    if expected_sid is not None:
        expected_resp_sid = (expected_sid + 0x40) & 0xFF
        if response_bytes[0] != expected_resp_sid:
            result["error"] = (
                f"SID mismatch: response SID 0x{response_bytes[0]:02X} "
                f"!= expected 0x{expected_resp_sid:02X} "
                f"(for request SID 0x{expected_sid:02X})"
            )
            return result
        # expected_echo is the variable-width generalization of expected_did;
        # a 2-byte expected_did becomes a 2-byte echo when no explicit echo given.
        echo = expected_echo
        if echo is None and expected_did is not None:
            echo = bytes([(expected_did >> 8) & 0xFF, expected_did & 0xFF])
        if echo:
            if len(response_bytes) < 1 + len(echo):
                result["error"] = (
                    f"Response too short for echo: got {len(response_bytes)} bytes, "
                    f"need >= {1 + len(echo)}"
                )
                return result
            got = response_bytes[1 : 1 + len(echo)]
            if got != echo and not _hk_identity_offset(expected_sid, echo, got):
                result["error"] = (
                    f"Echo mismatch: response id 0x{got.hex().upper()} "
                    f"!= expected 0x{echo.hex().upper()} "
                    f"(for request SID 0x{expected_sid:02X})"
                )
                return result

    result["ok"] = True
    return result
