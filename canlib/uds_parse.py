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
from typing import NotRequired, TypedDict

# Exchange-outcome taxonomy. Every parsed response falls into exactly one
# category; on a failure the same value is also stamped onto the response as
# ``error_kind`` (set at the error site, so classification never has to re-parse
# an error *string*). `canlib.transport_stats.TransportStats` tallies these per
# transport, the monitor surfaces them, and recorded captures carry the counts.
CAT_OK = "ok"  # positive, echo-matching response
CAT_NRC = "nrc"  # legitimate negative response (7F …) — an answer, not a fault
CAT_NO_DATA = "no_data"  # NO DATA / empty — the ECU said nothing
CAT_DROP = "drop"  # ISO-TP consecutive frame dropped (truncated reassembly)
CAT_STALE = "stale"  # stale/leaked frame: non-contiguous counters, SID/echo mismatch
CAT_DECODE = "decode"  # non-hex / too-short / undecodable payload
CAT_BUS = "bus"  # CAN ERROR / unable to connect / buffer full / bus init
CAT_OTHER = "other"  # unclassified fallback

# All categories a recorded exchange can fall into (drives TransportStats init).
RESPONSE_CATEGORIES: tuple[str, ...] = (
    CAT_OK,
    CAT_NRC,
    CAT_NO_DATA,
    CAT_DROP,
    CAT_STALE,
    CAT_DECODE,
    CAT_BUS,
    CAT_OTHER,
)

# The subset that means something went wrong (not a clean positive / valid NRC).
ERROR_CATEGORIES: tuple[str, ...] = (
    CAT_NO_DATA,
    CAT_DROP,
    CAT_STALE,
    CAT_DECODE,
    CAT_BUS,
    CAT_OTHER,
)


class UdsResponse(TypedDict):
    """Structured result of :func:`parse_uds_response` — the shape every transport returns.

    Both the ``wican-ws`` (:class:`canlib.terminal.WiCANTerminal`) and
    ``slcan-tcp`` (:class:`canlib.transport.raw_terminal.RawTerminal`) paths
    funnel their raw response through :func:`parse_uds_response`, so downstream
    code sees this identical shape regardless of transport. ``raw``/``ok`` are
    always present; the rest depend on the outcome:

    - a parseable positive carries ``hex``/``bytes`` (and ``isotp_declared_len``
      on a multi-frame read that reported an ISO-TP First Frame length);
    - a negative (``7F``) carries ``nrc``/``nrc_service``/``nrc_desc`` — unless
      the NRC echoes a *different* service, in which case those are dropped and
      ``error`` is set instead;
    - a parse failure / ``NO DATA`` / echo mismatch carries ``error``.
    """

    raw: str  # always: original response text
    ok: bool  # always: positive + echo-matching?
    hex: NotRequired[str]  # parseable positive: uppercased, space-stripped
    bytes: NotRequired[bytes]  # parseable positive
    isotp_declared_len: NotRequired[int]  # multi-frame: ISO-TP First Frame length
    nrc: NotRequired[int]  # negative (7F): the NRC byte
    nrc_service: NotRequired[int]  # negative: echoed service byte
    nrc_desc: NotRequired[str]  # negative: human description
    error: NotRequired[str]  # parse failure / NO DATA / echo mismatch
    error_kind: NotRequired[str]  # failure category (a CAT_* value), set with `error`


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


def classify_response(resp: UdsResponse) -> str:
    """Classify a parsed response into one :data:`RESPONSE_CATEGORIES` value.

    A clean positive is ``ok`` and a legitimate negative (``7F …``) is ``nrc``
    (an answer, not a fault). Every other outcome reports the ``error_kind``
    stamped at the point of failure; for a response carrying only an ``error``
    string (built outside this parser) it falls back to :func:`classify_error`
    on that text, and finally to ``other`` so a total never silently disappears.
    This is the single taxonomy both transports feed the diagnostics recorder,
    so drops/stale-frames/timeouts count identically regardless of transport.
    """
    if resp.get("ok"):
        return CAT_OK
    if resp.get("nrc") is not None:
        return CAT_NRC
    return resp.get("error_kind") or classify_error(resp.get("error"))


# Ordered (substring → category) rules matched case-insensitively against an
# error string. First match wins, so order matters (drops before the generic
# reassembly wording, mismatch before the rest). This is the fallback path for
# a response that carries an ``error`` string but no structured ``error_kind``
# (e.g. an error built outside :func:`parse_uds_response`); the parser itself
# stamps ``error_kind`` directly, so classification rarely needs the text.
_ERROR_RULES: tuple[tuple[str, str], ...] = (
    ("non-contiguous iso-tp", "drop"),
    ("truncated iso-tp", "drop"),
    ("mismatch", "stale"),
    ("no data", "no_data"),
    ("no response", "no_data"),
    ("empty response", "no_data"),
    ("timeout", "no_data"),
    ("can bus error", "bus"),
    ("unable to connect", "bus"),
    ("bus initialization", "bus"),
    ("connection closed", "bus"),
    ("request stopped", "bus"),
    ("buffer full", "bus"),
    ("non-hex", "decode"),
    ("hex decode", "decode"),
    ("too short", "decode"),
    ("odd hex", "decode"),
    ("unknown command", "decode"),
)


def classify_error(error: str | None) -> str:
    """Classify a UDS error string into an :data:`ERROR_CATEGORIES` bucket.

    Matches known substrings emitted by :func:`parse_uds_response` (and the raw
    clients); anything unrecognised falls back to ``"other"`` so a total never
    silently disappears. Pure/side-effect-free — safe to call on the hot path.
    """
    if not error:
        return "other"
    low = error.lower()
    for needle, category in _ERROR_RULES:
        if needle in low:
            return category
    return "other"


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
    tolerates it for F1xx DIDs **when the profile opts into the quirk** (see
    :data:`canlib.quirks.HK_F1XX_MINUS_ONE`); off for make-neutral profiles.
    """
    return (
        expected_sid == 0x22
        and len(expected_id) == 2
        and len(got) == 2
        and expected_id[0] == 0xF1
        and got[0] == 0xF1
        and got[1] == (expected_id[1] - 1) & 0xFF
    )


def payload_echo_mismatch(
    request_pid: str, payload_hex: str, hk_f1xx_offset: bool = False
) -> str | None:
    """Cross-check a stored capture payload against the PID it was recorded for.

    Returns a human-readable reason string when the payload's SID+identifier
    echo does NOT match ``request_pid`` (e.g. a ``6101…`` payload filed under a
    ``2102`` request — the ELM327 stale-frame bug), or ``None`` when it matches
    or when we can't validate (non-identifier request, NRC/short payload, hex we
    can't parse). Only known echo mismatches are reported, so this is safe to
    surface as a soft lint warning.

    ``hk_f1xx_offset``: when True (the profile opts into the HK F1xx -1 quirk),
    an F1xx identity DID answering one *less* than requested is tolerated.
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
        # offset: 22F188 -> 62F187, etc.). On an HK profile that's expected ECU
        # behaviour, not a misfiled frame, so don't flag exact off-by-one F1xx.
        if hk_f1xx_offset and _hk_identity_offset(expected_sid, expected_id, got):
            return None
        return (
            f"payload echoes id 0x{got.hex().upper()} but request {request_pid} "
            f"expects 0x{expected_id.hex().upper()} (stale/misfiled frame?)"
        )
    return None


def payload_not_hex(payload: str) -> str | None:
    """Reason a stored capture payload is not a valid UDS byte string, else None.

    A capture payload should be an even-length run of hex digits (the raw
    response bytes). Anything else is a mis-recorded capture: an ELM327/status
    string kept verbatim (``NO DATA``, ``CAN ERROR``, ``5001 at attempts …``),
    free-text notes, or a mixed hex+ASCII transcription (e.g. a part-number
    ``62F18791950G7510`` where the ``G`` breaks pure-hex). Reported as a soft
    lint warning, never an error — payloads are recorded by the tool, so a
    non-hex one signals a bug/manual edit rather than a schema violation.
    """
    if not payload:
        return None
    cleaned = str(payload).replace(" ", "").strip()
    if len(cleaned) < 2:
        return f"payload too short to be a UDS response: {payload!r}"
    bad = {c for c in cleaned.upper() if c not in "0123456789ABCDEF"}
    if bad:
        return f"payload is not hex (contains {''.join(sorted(bad))!r}): {str(payload)[:40]!r}"
    if len(cleaned) % 2 != 0:
        return f"payload has an odd hex length ({len(cleaned)} nibbles): {str(payload)[:40]!r}"
    return None


def parse_uds_response(
    raw: str,
    expected_sid: int | None = None,
    expected_did: int | None = None,
    expected_echo: bytes | None = None,
    hk_f1xx_offset: bool = False,
) -> UdsResponse:
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

    ``hk_f1xx_offset``: when True (the profile opts into the HK F1xx -1 quirk),
    an F1xx identity DID answering one *less* than requested passes echo
    validation instead of being rejected as a stale/misfiled frame.
    """
    result: UdsResponse = {"raw": raw, "ok": False}

    def _fail(kind: str, msg: str) -> UdsResponse:
        """Set ``error`` + its ``error_kind`` (a CAT_* category) and return."""
        result["error"] = msg
        result["error_kind"] = kind
        return result

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
            return _fail(CAT_BUS, "Unknown command")
        if line == "NO DATA":
            return _fail(CAT_NO_DATA, "No response from ECU (NO DATA)")
        if line == "CAN ERROR":
            return _fail(CAT_BUS, "CAN bus error")
        if line == "UNABLE TO CONNECT":
            return _fail(CAT_BUS, "Unable to connect to CAN bus")
        if line == "BUS INIT: ...ERROR":
            return _fail(CAT_BUS, "Bus initialization error")
        if line == "STOPPED":
            return _fail(CAT_BUS, "Request stopped")
        if line == "BUFFER FULL":
            return _fail(CAT_BUS, "Response buffer full")
        data_lines.append(line)

    if not data_lines:
        return _fail(CAT_NO_DATA, "Empty response")

    # Filter out echo of the request
    if len(data_lines) > 1:
        first = data_lines[0].replace(" ", "")
        if len(first) >= 2 and all(c in "0123456789ABCDEFabcdef" for c in first):
            first_byte = int(first[:2], 16)
            if 0x10 <= first_byte <= 0x3E:
                data_lines = data_lines[1:]

    # Check for multi-frame ISO-TP format. The ELM327 prefixes each numbered
    # frame line with a SINGLE hex-digit counter (0..F) that wraps back to 0
    # after F, so responses with 16+ frames use A..F and repeat — the prefix is
    # hex, not decimal.
    is_multiframe = any(re.match(r"^[0-9A-Fa-f]:", line) for line in data_lines)

    # ELM327 multi-frame output precedes the numbered frame lines with a bare-hex
    # total-length token (the ISO-TP First Frame length), e.g. `017` = 23 bytes:
    #   22C00B\r 017 \r 0:62C00BFFFF00 \r 1:00C84F0100C84E \r 2:... \r 3:...
    # Capture it so we can reject truncated multi-frame reads (a dropped consecutive
    # frame silently misaligns every downstream byte) rather than store them.
    declared_len: int | None = None

    if is_multiframe:
        # The ELM327 emits frame lines in transmission order, so arrival order —
        # not the printed counter — is authoritative. Reassemble in order and
        # verify each line's single hex-digit counter is the next value in the
        # wrapping 0..F,0..F,… sequence. A missing, duplicate, or out-of-order
        # counter means a frame was dropped or a stale frame from a prior request
        # leaked in, so the concatenation would silently misalign every
        # downstream byte. A gap can be masked by trailing padding (total length
        # still >= declared), so the length guard below can't catch it — check
        # contiguity explicitly. Unwrapping by arrival order (rather than sorting
        # the printed nibble) is what makes 16+ frame responses reassemble
        # correctly, where the counter wraps and nibbles collide.
        frame_hex: list[str] = []
        counters: list[str] = []  # printed nibbles, for the error message
        contiguous = True
        expected_counter = 0
        for line in data_lines:
            m = re.match(r"^([0-9A-Fa-f]):([0-9A-Fa-f]+)$", line)
            if m:
                counters.append(m.group(1).upper())
                if int(m.group(1), 16) != (expected_counter & 0xF):
                    contiguous = False
                frame_hex.append(m.group(2))
                expected_counter += 1
                continue
            # A non-frame, bare-hex line before the frames is the length token.
            if declared_len is None and re.fullmatch(r"[0-9A-Fa-f]{1,4}", line):
                declared_len = int(line, 16)
        if not contiguous:
            result["error"] = (
                f"non-contiguous ISO-TP frames: line counters {counters} are not a "
                f"contiguous 0,1,…,F(,0,…) run (dropped/duplicate/stale frame)"
            )
            result["error_kind"] = CAT_DROP
            return result
        hex_clean = "".join(frame_hex)
    else:
        hex_str = " ".join(data_lines)
        hex_clean = hex_str.replace(" ", "")

    if not all(c in "0123456789ABCDEFabcdef" for c in hex_clean):
        return _fail(CAT_DECODE, f"Non-hex response: {hex_clean[:80]}")

    if len(hex_clean) < 2:
        return _fail(CAT_DECODE, f"Response too short: {hex_clean}")

    try:
        response_bytes = bytes.fromhex(hex_clean)
    except ValueError as e:
        return _fail(CAT_DECODE, f"Hex decode failed: {e}")

    # Reject truncated multi-frame reads. The ELM327 adapter reassembles ISO-TP
    # and reports the declared total length; if the frames it handed back fall
    # short of that (a dropped consecutive frame), every byte after the gap is
    # misaligned and the payload is worthless. Trailing ISO-TP padding may make
    # the reassembly *longer* than declared, which is fine; only short is fatal.
    if declared_len is not None:
        result["isotp_declared_len"] = declared_len
        if len(response_bytes) < declared_len:
            result["error"] = (
                f"truncated ISO-TP: got {len(response_bytes)} bytes, declared {declared_len}"
            )
            result["error_kind"] = CAT_DROP
            return result
        # Drop any trailing ISO-TP padding beyond the declared payload length.
        if len(response_bytes) > declared_len:
            response_bytes = response_bytes[:declared_len]
            hex_clean = response_bytes.hex()

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
            result["error_kind"] = CAT_STALE
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
            result["error_kind"] = CAT_STALE
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
                result["error_kind"] = CAT_DECODE
                return result
            got = response_bytes[1 : 1 + len(echo)]
            if got != echo and not (
                hk_f1xx_offset and _hk_identity_offset(expected_sid, echo, got)
            ):
                result["error"] = (
                    f"Echo mismatch: response id 0x{got.hex().upper()} "
                    f"!= expected 0x{echo.hex().upper()} "
                    f"(for request SID 0x{expected_sid:02X})"
                )
                result["error_kind"] = CAT_STALE
                return result

    result["ok"] = True
    return result
