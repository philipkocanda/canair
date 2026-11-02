"""Turning a transport's response text into a :class:`~canlib.uds.response.UdsResponse`.

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
(:mod:`canlib.uds.response`), NRC names (:mod:`canlib.uds.nrc`) and the request echo
(:mod:`canlib.uds.echo`).
"""

from __future__ import annotations

import re

from canlib.uds.echo import echo_mismatch
from canlib.uds.nrc import nrc_description
from canlib.uds.response import (
    CAT_BUS,
    CAT_DECODE,
    CAT_DROP,
    CAT_NO_DATA,
    CAT_STALE,
    UdsResponse,
)
from canlib.uds.services import NEGATIVE_RESPONSE_SID

# Adapter status lines that end a reply: line -> (category, message). Each message
# must classify to the same category under `canlib.uds.response.classify_error`.
# `?` (command not understood) is a complete, prompt-terminated refusal that leaves
# nothing queued, so it is a bus-side failure rather than a dirty pipe.
ADAPTER_STATUS: dict[str, tuple[str, str]] = {
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
# UDS ResponsePending (`7F <sid> 78`): an interim "still working" frame.
_PENDING_LINE = re.compile(r"7F[0-9A-F]{2}78")


def _compact(line: str) -> str:
    return line.replace(" ", "").upper()


def _is_hex(text: str) -> bool:
    return bool(text) and set(text) <= _HEX_DIGITS


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
    (:func:`canlib.transport.elm327_frame_count.annotate_request`). A positive reply
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
        return first == sent or (
            len(first) == len(sent) + 1 and first.startswith(sent) and first[-1] in _HEX_DIGITS
        )
    if len(data_lines) < 2 or len(first) < 2 or not _is_hex(first):
        return False
    return 0x10 <= int(first[:2], 16) <= 0x3E


class _Rejected(Exception):
    """A reply that cannot be read, carrying its outcome category and message."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _adapter_lines(raw: str) -> list[str]:
    """The reply's data lines; raises :class:`_Rejected` on an adapter status line."""
    data_lines: list[str] = []
    for line in raw.split("\n"):
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
    if not any(_FRAME_LINE.match(line) for line in upper):
        # Each unprefixed line is one CAN frame, so a single-frame reply counts 1.
        # Several lines here means several responders to one request.
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
        # reply's reassembly.
        if not frame_hex and _LENGTH_TOKEN.fullmatch(line):
            declared_len = int(line, 16)
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
    hk_f1xx_offset: bool = False,
    protocol: str | None = None,
    request: str | None = None,
) -> UdsResponse:
    """Parse a UDS response (as returned by any transport) into structured data.

    Args:
        raw: Raw response text — ELM327 terminal output, or the hex line
            :class:`~canlib.transport.raw_terminal.RawTerminal` formats from the
            reassembled ISO-TP payload.
        expected_sid: If set, the response must answer this request SID: a positive
            one starts with ``expected_sid + 0x40`` and a negative one names it as
            its service. A mismatch is a stale/misaligned reply (``error_kind``
            ``stale``) — a late frame from an earlier request leaking into this one.
        expected_echo: If set with ``expected_sid``, the identifier bytes a positive
            response must repeat right after its SID (the 1-byte service-21 PID, the
            2-byte service-22/2F DID). Derive it with
            :func:`canlib.uds.echo.request_echo`.
        hk_f1xx_offset: The profile opts into the HK F1xx -1 quirk, so an F1xx
            identity DID answering one *less* than requested passes echo validation.
        protocol: The target ECU's diagnostic protocol, which selects the NRC names
            in ``nrc_desc`` (see :func:`canlib.uds.nrc.nrc_description`).
        request: The command as sent (hex, with or without the ELM327 frame-count
            digit). An adapter echo is then recognised by equality rather than
            guessed at; see :func:`_is_request_echo`. Pass it whenever it is known.
    """
    result: UdsResponse = {"raw": raw, "ok": False}

    def _fail(kind: str, msg: str) -> UdsResponse:
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
    # An interim ResponsePending frame ahead of the answer is not part of it; only
    # when nothing followed is it reported (as the NRC it is).
    while len(lines) > 1 and _PENDING_LINE.fullmatch(_compact(lines[0])):
        lines = lines[1:]

    try:
        hex_clean, frame_count, declared_len = _reassemble(lines)
    except _Rejected as rejected:
        return _fail(rejected.kind, rejected.message)

    # How many frames the adapter actually handed back. This is the ground truth
    # the ELM327 expected-response-count optimization validates its learned count
    # against (see canlib/transport/elm327_frame_count.py); recorded before the
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
        problem = echo_mismatch(
            expected_sid, expected_echo or b"", response_bytes, hk_f1xx_offset=hk_f1xx_offset
        )
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

    result["ok"] = True
    return result
