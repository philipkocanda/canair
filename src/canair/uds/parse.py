"""Turning a transport's response text into a :class:`~canair.uds.response.UdsResponse`.

Transport-independent: both the ELM327 transports (``wican-ws``/``elm327-tcp``, whose
adapter does ISO-TP) and ``slcan-tcp`` (client-side ISO-TP) funnel their response
through :func:`parse_uds_response`, so downstream code sees an identical result
regardless of which transport produced the bytes. The raw path hands it one line of
already-reassembled hex; the ELM327 path hands it terminal output, with the adapter's
artifacts — request echoes, status lines (``NO DATA``, ``CAN ERROR``), ``>`` prompts,
flow-control echoes, interim ResponsePending frames and numbered multi-frame lines —
which this module strips or reassembles. It assumes headers off (``ATH0``), which
every profile init and the engine keep; a reply printed with headers is not a
UDS payload this parser can read.

The pieces it judges with live beside it: the response shape and outcome categories
(:mod:`canair.uds.response`), NRC names (:mod:`canair.uds.nrc`) and the request echo
(:mod:`canair.uds.echo`).
"""

from __future__ import annotations

import re

from canair.uds.echo import echo_mismatch, reply_check, request_echo
from canair.uds.nrc import is_response_pending, nrc_description
from canair.uds.protocol import DiagProtocol
from canair.uds.response import (
    CAT_BUS,
    CAT_DECODE,
    CAT_DROP,
    CAT_NO_DATA,
    CAT_STALE,
    ResponseCategory,
    UdsResponse,
)
from canair.uds.services import (
    NEGATIVE_RESPONSE_SID,
    READ_MEMORY_BY_ADDRESS,
    REQUEST_SID_RANGE,
    RESPONSE_SID_OFFSET,
)

_MEMORY_SID_HEX = f"{READ_MEMORY_BY_ADDRESS:02X}"

# Adapter status lines that end a reply: line -> (category, message). Each message
# must classify to the same category under `canair.uds.response.classify_error`.
# `?` (command not understood) is a complete, prompt-terminated refusal that leaves
# nothing queued, so it is a bus-side failure rather than a dirty pipe.
ADAPTER_STATUS: dict[str, tuple[ResponseCategory, str]] = {
    "?": (CAT_BUS, "Unknown command"),
    "NO DATA": (CAT_NO_DATA, "No response from ECU (NO DATA)"),
    "CAN ERROR": (CAT_BUS, "CAN bus error"),
    "BUS ERROR": (CAT_BUS, "CAN bus error (BUS ERROR)"),
    "BUS BUSY": (CAT_BUS, "CAN bus busy"),
    "UNABLE TO CONNECT": (CAT_BUS, "Unable to connect to CAN bus"),
    "BUS INIT: ...ERROR": (CAT_BUS, "Bus initialization error"),
    "STOPPED": (CAT_BUS, "Request stopped"),
    "BUFFER FULL": (CAT_BUS, "Response buffer full"),
}

# Adapter progress lines that precede a reply and carry none of it. `SEARCHING...`
# is what an adapter left on automatic protocol detection (`ATSP0`) prints first.
_ADAPTER_INFO = frozenset({"OK", "SEARCHING..."})

_HEX_DIGITS = frozenset("0123456789ABCDEF")

# One numbered multi-frame line: a single hex-digit counter, then that frame's bytes
# (`0:62C00B…`, or `0: 62 C0 0B …` with spaces on).
_FRAME_LINE = re.compile(r"([0-9A-F])\s*:\s*([0-9A-F][0-9A-F ]*)")
# The bare-hex ISO-TP First Frame length an adapter prints before the frame lines.
_LENGTH_TOKEN = re.compile(r"[0-9A-F]{1,4}")
# The smallest First Frame length: 7 bytes or fewer travel as one Single Frame.
_MIN_FIRST_FRAME_LEN = 8


def compact(text: str) -> str:
    """Strip spaces and line breaks, so hex tests work with ``ATS1`` and ``ATS0``."""
    return text.replace(" ", "").replace("\r", "").replace("\n", "")


def reply_lines(text: str) -> list[bytes]:
    """Each line of adapter text that is whole hex bytes, as bytes, in order.

    For code that reads raw adapter output instead of one parsed reply: a line counts only
    when all of it is hex, so bytes inside a longer reply, or split across two lines, never
    read as a reply of their own. Prompts, status text and odd-length lines (a flow-control
    echo such as ``F00``) are left out.
    """
    out = []
    for line in text.replace("\r", "\n").split("\n"):
        hex_text = _compact(line.strip().rstrip(">"))
        if len(hex_text) >= 2 and len(hex_text) % 2 == 0 and _is_hex(hex_text):
            out.append(bytes.fromhex(hex_text))
    return out


def _compact(line: str) -> str:
    return compact(line).upper()


def _is_hex(text: str) -> bool:
    return bool(text) and set(text) <= _HEX_DIGITS


def _is_pending_line(line: str) -> bool:
    """True for a line that is exactly an interim ResponsePending (``7F <sid> 78``).

    The whole line must be the 3-byte NRC, so a reply merely *containing* those bytes
    (``62 01 7F 22 78``) is never taken for one.
    """
    text = _compact(line)
    return len(text) == 6 and _is_hex(text) and is_response_pending(bytes.fromhex(text))


def _is_flow_control_echo(line: str) -> bool:
    """True for the adapter's echo of a Flow Control frame it sent (``F00``…).

    No reply starts with ``F`` (positive response SIDs are ``0x41``-``0x7F``, a negative
    one ``7F``), so a short line opening ``F0`` is never data. With headers printed the
    echo carries one (``770 F00``), recognised only when a space sets it apart: a
    contiguous ``620F00`` is the reply ``62 0F 00``, not a header plus an echo.
    """
    text = line.upper()
    head, sep, rest = text.partition(" ")
    body = rest if sep and len(head) == 3 and _is_hex(head) else text
    body = body.replace(" ", "")
    return body.startswith("F0") and len(body) <= 8 and _is_hex(body)


def _is_request_echo(data_lines: list[str], request: str | None) -> bool:
    """True when the first data line is the adapter's echo of the request.

    An ELM327 with echo on (``ATZ`` leaves it on, and canair never sends ``ATE0``)
    repeats each command before its reply. With the request known, only a line
    *equal to it* is an echo — spaces and case aside, and optionally followed by the
    one-digit expected-frame count canair appends
    (:func:`canair.transport.elm327.frame_count.annotate_request`). A positive reply
    starts with SID + 0x40 and a negative one with ``7F``, so no reply can equal its
    request; a guess by first byte could not say the same, since the bare ISO-TP
    length token that opens a multi-frame reply with echo off (``017``, ``117``) and
    every OBD-II mode (``01``–``0A``) look alike.

    Without the request, the fallback guesses by the first byte: any UDS/KWP2000
    request SID (``0x10``–``0x3E``) on a line followed by another one.
    """
    if not data_lines:
        return False
    first = _compact(data_lines[0])
    if request is not None:
        sent = _compact(request)
        if not sent:
            return False
        if first == sent:
            return True
        # The request plus one count digit — unless the next line is frame 0, which
        # makes the line the ISO-TP length token of an echo-off reply (`03A` after a
        # `03` request), not an echo.
        follows_frame = len(data_lines) > 1 and _FRAME_LINE.match(data_lines[1].upper())
        return (
            len(first) == len(sent) + 1
            and first.startswith(sent)
            and first[-1] in _HEX_DIGITS
            and not follows_frame
        )
    if len(data_lines) < 2 or len(first) < 2 or not _is_hex(first):
        return False
    return int(first[:2], 16) in REQUEST_SID_RANGE


class _Rejected(Exception):
    """A reply that cannot be read, carrying its outcome category and message."""

    def __init__(self, kind: ResponseCategory, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _sent_request(request: str | None) -> tuple[int | None, bytes]:
    """``(sid, identifier echo)`` of the command as sent, as far as it can be read.

    The ELM327 expected-frame count digit a command may end in is not part of the
    request. The echo is empty for a request :func:`request_echo` cannot judge.
    """
    text = _compact(request or "")
    if len(text) % 2:
        text = text[:-1]
    derived = request_echo(text)
    if derived is not None:
        return derived
    return (int(text[:2], 16) if len(text) >= 2 and _is_hex(text[:2]) else None), b""


def _adapter_lines(raw: str) -> list[str]:
    """The reply's data lines; raises :class:`_Rejected` on an adapter status line."""
    data_lines: list[str] = []
    # An ELM327 ends lines with CR (``ATL0``, the default) or CR LF, a WebSocket
    # bridge often with LF alone.
    for line in raw.replace("\r", "\n").split("\n"):
        line = line.strip().rstrip(">").strip()
        if not line or line.upper().startswith("AT") or line in _ADAPTER_INFO:
            continue
        if _is_flow_control_echo(line):
            continue
        status = ADAPTER_STATUS.get(line)
        if status is not None:
            raise _Rejected(*status)
        data_lines.append(line)
    return data_lines


def _is_multiframe(lines: list[str]) -> bool:
    return any(_FRAME_LINE.match(line.upper()) for line in lines)


def _answers(reply: bytes, sid: int, echo: bytes) -> bool:
    """Does ``reply`` answer a request of service ``sid`` (and identifier ``echo``)?"""
    if reply[0] == NEGATIVE_RESPONSE_SID:
        return len(reply) >= 3 and reply[1] == sid
    return echo_mismatch(sid, echo, reply) is None


def _pick_reply(lines: list[str], sid: int | None, echo: bytes, *, validated: bool) -> str | None:
    """The one line answering the request among several single-frame lines.

    Several unprefixed lines are several separate messages — a late reply to an
    earlier request that landed in this one's window, or more than one ECU answering
    — and joining them would yield a payload no ECU sent. The line that answers the
    request is kept when exactly one does and nothing else in the block is another
    request's echo (which would mean the whole exchange belongs to an earlier
    command, so this one's answer has not arrived). When no line answers, the first
    is returned for the caller's echo check to report — if ``validated`` says there
    is one; otherwise that is refused here. Returns None when a line is not hex,
    leaving the caller's decode check to report it; raises :class:`_Rejected` when
    the lines cannot be told apart.
    """
    replies = [bytes.fromhex(c) for c in map(_compact, lines) if _is_hex(c) and len(c) % 2 == 0]
    if len(replies) != len(lines):
        return None
    if sid is None:
        raise _Rejected(
            CAT_STALE,
            f"{len(lines)} separate replies and no request to tell them apart: "
            + ", ".join(r.hex().upper()[:16] for r in replies),
        )
    answering = [r for r in replies if _answers(r, sid, echo)]
    if not answering:
        if validated:
            return _compact(lines[0])  # the first one's mismatch is the error to report
        raise _Rejected(
            CAT_STALE,
            f"Echo mismatch: none of {len(lines)} separate replies answers request SID 0x{sid:02X}",
        )
    if len(set(answering)) > 1:
        raise _Rejected(
            CAT_STALE,
            f"{len(set(answering))} different replies answer request SID 0x{sid:02X} "
            "(more than one ECU responded?)",
        )
    if any(r[0] < RESPONSE_SID_OFFSET for r in replies if r not in answering):
        raise _Rejected(
            CAT_STALE,
            "the reply holds another request's echo, so it belongs to an earlier "
            "command (stale/misaligned exchange)",
        )
    return answering[0].hex().upper()


def _reassemble(data_lines: list[str]) -> tuple[str, int, int | None]:
    """``(hex, frame_count, declared_len)`` of the reply; raises :class:`_Rejected`.

    The ELM327 prints a multi-frame reply as a bare-hex total length (the ISO-TP First
    Frame length, e.g. ``017`` = 23 bytes) followed by numbered frame lines::

        017
        0:62C00BFFFF00
        1:00C84F0100C84E
        2:...

    The adapter emits frame lines in transmission order, so arrival order — not the
    printed counter — is authoritative: each line's single hex-digit counter must be
    the next value of the wrapping 0..F,0..F,… sequence. A missing, duplicate or
    out-of-order counter means a frame was dropped or a stale frame leaked in, which
    would silently misalign every later byte; the length check cannot catch it when
    trailing padding hides the gap. Unwrapping by arrival order (rather than sorting
    the printed nibble) is what reassembles a 16+ frame reply, where counters repeat.
    """
    upper = [line.upper() for line in data_lines]
    if not _is_multiframe(upper):
        # Each unprefixed line is one CAN frame, so a single-frame reply counts 1.
        return "".join(_compact(line) for line in upper), len(upper), None

    frame_hex: list[str] = []
    counters: list[str] = []
    declared_len: int | None = None
    for line in upper:
        frame = _FRAME_LINE.fullmatch(line)
        if frame:
            counters.append(frame.group(1))
            frame_hex.append(frame.group(2).replace(" ", ""))
            continue
        if _FRAME_LINE.match(line):
            raise _Rejected(CAT_DECODE, f"Non-hex response frame: {line[:80]}")
        # The length token is the last bare-hex line *before* the frames; anything
        # else (a stale single-frame reply that preceded it, say) is not part of this
        # reply's reassembly. A First Frame length is never below 8 on classic CAN (a
        # shorter reply fits one Single Frame), so a smaller token is not one.
        if not frame_hex and _LENGTH_TOKEN.fullmatch(line):
            length = int(line, 16)
            if length >= _MIN_FIRST_FRAME_LEN:
                declared_len = length
    if any(int(c, 16) != i & 0xF for i, c in enumerate(counters)):
        raise _Rejected(
            CAT_DROP,
            f"non-contiguous ISO-TP frames: line counters {counters} are not a "
            f"contiguous 0,1,…,F(,0,…) run (dropped/duplicate/stale frame)",
        )
    return "".join(frame_hex), len(frame_hex), declared_len


def parse_uds_response(
    raw: str,
    expected_sid: int | None = None,
    expected_echo: bytes | None = None,
    protocol: DiagProtocol | None = None,
    request: str | None = None,
) -> UdsResponse:
    """Parse a UDS response (as returned by any transport) into structured data.

    Args:
        raw: Raw response text — ELM327 terminal output, or the hex line
            :class:`~canair.transport.raw.terminal.RawTerminal` formats from the
            reassembled ISO-TP payload.
        expected_sid: If set, the response must answer this request SID: a positive
            one starts with ``expected_sid + 0x40`` and a negative one names it as
            its service. A mismatch is a stale/misaligned reply (``error_kind``
            ``stale``) — a late frame from an earlier request leaking into this one.
        expected_echo: If set with ``expected_sid``, the identifier bytes a positive
            response must repeat right after its SID (the 1-byte service-21 PID, the
            2-byte service-22/2F DID). Derive it with
            :func:`canair.uds.echo.request_echo`.
        protocol: The target ECU's diagnostic protocol, which selects the NRC names
            in ``nrc_desc`` (see :func:`canair.uds.nrc.nrc_description`).
        request: The command as sent (hex, with or without the ELM327 frame-count
            digit). An adapter echo is then recognised by equality rather than
            guessed at; see :func:`_is_request_echo`. Pass it whenever it is known.
    """
    result: UdsResponse = {"raw": raw, "ok": False}

    def _fail(kind: ResponseCategory, msg: str) -> UdsResponse:
        result["error"] = msg
        result["error_kind"] = kind
        return result

    try:
        lines = _adapter_lines(raw)
    except _Rejected as rejected:
        return _fail(rejected.kind, rejected.message)
    if not lines:
        return _fail(CAT_NO_DATA, "Empty response")
    if _is_request_echo(lines, request):
        lines = lines[1:]
        if not lines:
            return _fail(CAT_NO_DATA, "Empty response (only the request echo)")
    # An interim ResponsePending frame is not an answer, wherever it sits — ahead of
    # the reply, or between a late reply to an earlier request and this one's. Only
    # when it is the last line is it reported (as the NRC it is): the answer it
    # promises has not arrived.
    last = len(lines) - 1
    lines = [line for i, line in enumerate(lines) if i == last or not _is_pending_line(line)]
    if len(lines) > 1 and not _is_multiframe(lines):
        # Lines are told apart by what this request's answer must echo; what the
        # caller did not say is read off the request itself.
        sent_sid, sent_echo = _sent_request(request)
        sid = expected_sid if expected_sid is not None else sent_sid
        echo = expected_echo
        if echo is None:
            echo = sent_echo if sid == sent_sid else b""
        try:
            picked = _pick_reply(lines, sid, echo, validated=expected_sid is not None)
        except _Rejected as rejected:
            return _fail(rejected.kind, rejected.message)
        if picked is not None:
            lines = [picked]

    try:
        hex_clean, frame_count, declared_len = _reassemble(lines)
    except _Rejected as rejected:
        return _fail(rejected.kind, rejected.message)

    # How many frames the adapter actually handed back. This is the ground truth
    # the ELM327 expected-response-count optimization validates its learned count
    # against (see src/canair/transport/elm327/frame_count.py); recorded before the
    # truncation checks below so a short read still reports what did arrive.
    result["isotp_frame_count"] = frame_count

    if not _is_hex(hex_clean.upper()):
        return _fail(CAT_DECODE, f"Non-hex response: {hex_clean[:80]}")
    if len(hex_clean) < 2:
        return _fail(CAT_DECODE, f"Response too short: {hex_clean}")
    if len(hex_clean) % 2:
        return _fail(CAT_DECODE, f"Odd hex length response: {hex_clean[:80]}")
    response_bytes = bytes.fromhex(hex_clean)

    # A dropped consecutive frame misaligns every byte after the gap, so a reply
    # shorter than its declared length is worthless. Trailing ISO-TP padding may make
    # it *longer*, which is fine: the padding is cut off.
    if declared_len is not None:
        result["isotp_declared_len"] = declared_len
        if len(response_bytes) < declared_len:
            return _fail(
                CAT_DROP,
                f"truncated ISO-TP: got {len(response_bytes)} bytes, declared {declared_len}",
            )
        response_bytes = response_bytes[:declared_len]

    result["hex"] = response_bytes.hex().upper()
    result["bytes"] = response_bytes

    if response_bytes[0] == NEGATIVE_RESPONSE_SID:
        if len(response_bytes) < 3:
            return _fail(CAT_DECODE, f"Negative response too short: {result['hex']}")
        if expected_sid is not None and response_bytes[1] != expected_sid:
            # The NRC rejects a *different* service: a stale/misaligned frame, not
            # a real answer to our request, so no nrc fields are reported.
            return _fail(
                CAT_STALE,
                f"NRC echo mismatch: NRC service byte 0x{response_bytes[1]:02X} "
                f"!= expected SID 0x{expected_sid:02X}",
            )
        result["nrc"] = response_bytes[2]
        result["nrc_service"] = response_bytes[1]
        result["nrc_desc"] = nrc_description(response_bytes[2], protocol)
        return result

    if expected_sid is not None:
        problem = echo_mismatch(expected_sid, expected_echo or b"", response_bytes)
        if problem is not None and problem.field == "sid":
            return _fail(
                CAT_STALE,
                f"SID mismatch: response SID 0x{response_bytes[0]:02X} "
                f"!= expected 0x{problem.expected.hex().upper()} "
                f"(for request SID 0x{expected_sid:02X})",
            )
        if problem is not None and problem.field == "length":
            return _fail(
                CAT_DECODE,
                f"Response too short for echo: got {len(response_bytes)} bytes, "
                f"need >= {1 + len(problem.expected)}",
            )
        if problem is not None:
            return _fail(
                CAT_STALE,
                f"Echo mismatch: response id 0x{problem.got.hex().upper()} "
                f"!= expected 0x{problem.expected.hex().upper()} "
                f"(for request SID 0x{expected_sid:02X})",
            )

    expected_len = _fixed_reply_length(request, protocol)
    if expected_len is not None and len(response_bytes) != expected_len:
        # A memory reply repeats nothing of its request, so its length is the only proof
        # that it answers this read: another read's reply, or one cut short, fails here.
        return _fail(
            CAT_STALE,
            f"Length mismatch: a memory read answered with {len(response_bytes)} bytes, "
            f"expected {expected_len} (another request's reply, or one cut short)",
        )

    result["ok"] = True
    return result


def _fixed_reply_length(request: str | None, protocol: DiagProtocol | None) -> int | None:
    """The exact positive-reply length ``request`` fixes (a memory read), or None.

    The ELM327 expected-frame count digit a command may end in is not part of the request.
    """
    text = _compact(request or "")
    if len(text) % 2:
        text = text[:-1]
    if not text.upper().startswith(_MEMORY_SID_HEX):
        return None
    check = reply_check(text, protocol)
    return check.length if check is not None else None
