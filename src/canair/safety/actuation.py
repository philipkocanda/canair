"""What a diagnostic request does to the car: ``read``, ``release`` or ``actuate``.

The request classifier from ``plans/2026-10-22-actuation-safety-interlock.md`` §1:
an allowlist, so a service nobody has classified fails closed as ``actuate``.
Service facts (names, identifier widths) stay in :mod:`canair.uds.services`; this
module only adds what a request *does*.

KWP2000 ``InputOutputControlByLocalIdentifier`` (``0x30``) has no standard release.
ISO 14230-3 leaves its control-option byte to the manufacturer, and the byte that
SSF 14230-3 calls returnControlToECU (``00``) actuates elsewhere: Nissan's ``00`` is
a one-shot command, and what it does on Hyundai/Kia is unknown. So a ``0x30``
request is a release only when
the profile declares it as one (an IOControl entry's ``release:``), and every other
``0x30`` request is ``actuate``.

UDS ``0x2F`` returnControlToECU carries no control state (ISO 14229-1:2013 Table E.1), so
whatever follows its parameter byte is the controlEnableMaskRecord of a packeted DID: the
request is a release with or without it. A packeted DID whose maker uses the mask concept
needs the mask on every request, release included (12.2.1), so such an entry declares its
``enable_mask`` and its implied release carries a mask of all ones, which every server must
accept to hand every parameter back.

Pure and transport-free. Nothing calls it from the transports yet: the interlock's
funnel is still a plan. ``canair io`` uses it to refuse an ON request that has no
known release (:func:`on_refusal`), and ``canair scan range`` to ask before it sweeps
a service that can change the car's state
(:func:`canair.live.scan.range.confirm_actuating_scan`).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Final, Literal

from canair.safety.blocklist import check_command_safety
from canair.uds.protocol import KWP, UDS, DiagProtocol
from canair.uds.requests import io_control_request
from canair.uds.services import (
    DIAGNOSTIC_SESSION_CONTROL,
    INPUT_OUTPUT_CONTROL_BY_IDENTIFIER,
    KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER,
    KWP_READ_DATA_BY_LOCAL_IDENTIFIER,
    KWP_READ_DTC_BY_STATUS,
    KWP_READ_DTCS,
    KWP_READ_ECU_IDENTIFICATION,
    KWP_READ_FREEZE_FRAME_DATA,
    KWP_READ_STATUS_OF_DTCS,
    KWP_REQUEST_ROUTINE_RESULTS_BY_ADDRESS,
    KWP_REQUEST_ROUTINE_RESULTS_BY_LOCAL_IDENTIFIER,
    KWP_STOP_DIAGNOSTIC_SESSION,
    KWP_STOP_ROUTINE_BY_LOCAL_IDENTIFIER,
    OBD_REQUEST_VEHICLE_INFORMATION,
    OBD_SHOW_CURRENT_DATA,
    OBD_SHOW_FREEZE_FRAME_DATA,
    OBD_SHOW_PENDING_DTCS,
    OBD_SHOW_PERMANENT_DTCS,
    OBD_SHOW_STORED_DTCS,
    READ_DATA_BY_IDENTIFIER,
    READ_DTC_INFORMATION,
    READ_MEMORY_BY_ADDRESS,
    ROUTINE_CONTROL,
    SECURITY_ACCESS,
    TESTER_PRESENT,
    identifier_width,
)
from canair.uds.subfunctions import (
    IoControlParameter,
    KwpSessionType,
    RoutineControlType,
    SessionType,
)

RequestClass = Literal["read", "release", "actuate"]
READ: Final[RequestClass] = "read"
RELEASE: Final[RequestClass] = "release"
ACTUATE: Final[RequestClass] = "actuate"

# UDS InputOutputControlParameter (ISO 14229-1): 00 returnControlToECU. 01-03
# (resetToDefault, freezeCurrentState, shortTermAdjustment) drive the output.
_UDS_RETURN_CONTROL = IoControlParameter.RETURN_CONTROL_TO_ECU

# Services that only read, or only move the diagnostic session, on every protocol:
# the OBD-II read modes, DTC and data reads, SecurityAccess and TesterPresent. Session
# control is judged by its session type (_session_class). 0x04 (OBD-II clear) and 0x14 (clear diagnostic information) are
# deliberately absent: clearing DTCs changes ECU state.
_READ_SERVICES: Final = frozenset(
    {
        OBD_SHOW_CURRENT_DATA,
        OBD_SHOW_FREEZE_FRAME_DATA,
        OBD_SHOW_STORED_DTCS,
        OBD_SHOW_PENDING_DTCS,
        OBD_REQUEST_VEHICLE_INFORMATION,
        OBD_SHOW_PERMANENT_DTCS,
        KWP_READ_FREEZE_FRAME_DATA,
        KWP_READ_DTCS,
        KWP_READ_STATUS_OF_DTCS,
        KWP_READ_DTC_BY_STATUS,
        READ_DTC_INFORMATION,
        KWP_READ_ECU_IDENTIFICATION,
        KWP_READ_DATA_BY_LOCAL_IDENTIFIER,
        READ_DATA_BY_IDENTIFIER,
        READ_MEMORY_BY_ADDRESS,
        SECURITY_ACCESS,
        TESTER_PRESENT,
    }
)

# KWP2000 services that read or hand control back, judged on a KWP2000 ECU (and,
# being KWP2000-only bytes, on an ECU of unknown protocol too).
_KWP_READ: Final = frozenset(
    {KWP_REQUEST_ROUTINE_RESULTS_BY_LOCAL_IDENTIFIER, KWP_REQUEST_ROUTINE_RESULTS_BY_ADDRESS}
)
# StopDiagnosticSession ends the session, and ISO 14230-3 has the ECU release every
# actuator the tester drove; StopRoutineByLocalIdentifier stops a started routine.
_KWP_RELEASE: Final = frozenset({KWP_STOP_DIAGNOSTIC_SESSION, KWP_STOP_ROUTINE_BY_LOCAL_IDENTIFIER})
_KWP_STOP_SESSION: Final = bytes([KWP_STOP_DIAGNOSTIC_SESSION])

# The UDS RoutineControl (0x31) sub-functions are RoutineControlType. On KWP2000 the same
# byte is StartRoutineByLocalIdentifier, which actuates whatever follows it.


@dataclass(frozen=True)
class IoRequest:
    """An IOControl request split into its fields.

    UDS ``0x2F``: ``2F {DID_HI DID_LO} {option} [state…]``. KWP2000 ``0x30``:
    ``30 {LID} {option} [state…]`` with an 8-bit local identifier (LID), whose
    ``option`` byte means whatever its manufacturer decided. ``option`` is None
    when the request stops after the identifier. ``mask`` is the UDS
    controlEnableMaskRecord, split off the end when its length is known.
    """

    service: int
    identifier: bytes
    option: int | None
    state: bytes
    mask: bytes = b""


def request_bytes(request: str | bytes) -> bytes:
    """A request as bytes, from bytes or hex text (spaces tolerated)."""
    if isinstance(request, bytes):
        return request
    text = request.replace(" ", "")
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise ValueError(f"not a hex request: {request!r}") from None


def normalize_request(request: str | bytes) -> str:
    """A request as upper-case hex without spaces, the form profiles store."""
    return request_bytes(request).hex().upper()


def parse_io_request(request: str | bytes, mask_length: int = 0) -> IoRequest | None:
    """Split an IOControl request (``0x2F`` or ``0x30``), or None for anything else.

    ``mask_length`` splits that many trailing bytes of a ``0x2F`` request off as its
    controlEnableMaskRecord, when the request is long enough to hold them. Returns None
    when the request is too short to carry its whole identifier.
    """
    data = request_bytes(request)
    if not data or data[0] not in (
        INPUT_OUTPUT_CONTROL_BY_IDENTIFIER,
        KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER,
    ):
        return None
    width = identifier_width(data[0])
    if len(data) < 1 + width:
        return None
    rest = data[1 + width :]
    state, mask = rest[1:], b""
    if data[0] == INPUT_OUTPUT_CONTROL_BY_IDENTIFIER and 0 < mask_length <= len(state):
        state, mask = state[:-mask_length], state[-mask_length:]
    return IoRequest(
        service=data[0],
        identifier=data[1 : 1 + width],
        option=rest[0] if rest else None,
        state=state,
        mask=mask,
    )


def classify(
    request: str | bytes,
    protocol: DiagProtocol,
    releases: Collection[str] = (),
) -> RequestClass:
    """Classify ``request`` for an ECU speaking ``protocol``.

    ``releases`` are the requests the profile declares as releases (IOControl
    ``release:`` values), the only way a KWP2000 ``0x30`` request classifies as
    ``release``. ELM327 ``AT`` commands configure the adapter and are ``read``.
    An unknown protocol gets the stricter reading wherever the two differ, except
    that ``0x2F`` keeps its UDS meaning there: it is the UDS service by far the
    most often, and a KWP2000 ECU is expected to declare its ``id_protocol``.
    """
    if isinstance(request, str) and request.strip().upper().startswith("AT"):
        return READ
    data = request_bytes(request)
    if not data:
        return ACTUATE
    sid = data[0]
    if normalize_request(data) in {normalize_request(r) for r in releases}:
        return RELEASE
    if sid in _READ_SERVICES:
        return READ
    if sid == DIAGNOSTIC_SESSION_CONTROL:
        return _session_class(data, protocol)
    if sid == INPUT_OUTPUT_CONTROL_BY_IDENTIFIER:
        # Any bytes after returnControlToECU are its controlEnableMaskRecord (Table E.1).
        if protocol != KWP and len(data) >= 4 and data[3] == _UDS_RETURN_CONTROL:
            return RELEASE
        return ACTUATE
    if sid == ROUTINE_CONTROL:
        if protocol == UDS and len(data) >= 2:
            if data[1] == RoutineControlType.REQUEST_ROUTINE_RESULTS:
                return READ
            if data[1] == RoutineControlType.STOP_ROUTINE:
                return RELEASE
        return ACTUATE
    if protocol != UDS:
        if sid in _KWP_READ:
            return READ
        if sid in _KWP_RELEASE:
            return RELEASE
    return ACTUATE


# The standard diagnostic sessions, which only change what the ECU answers. Any other session
# type is a programming, safety-system or manufacturer mode: Hyundai/Kia's 10 D1 puts every
# ECU to sleep, so a session request is not a read by its service alone.
_UDS_READ_SESSION_TYPES: Final = frozenset(
    {SessionType.DEFAULT_SESSION, SessionType.EXTENDED_DIAGNOSTIC_SESSION}
)
_KWP_READ_SESSION_TYPES: Final = _UDS_READ_SESSION_TYPES | {
    KwpSessionType.STANDARD_DIAGNOSTIC_SESSION,
    KwpSessionType.PERIODIC_DIAGNOSTIC_SESSION,
    KwpSessionType.EXTENDED_DIAGNOSTIC_SESSION,
}


def _session_class(data: bytes, protocol: DiagProtocol) -> RequestClass:
    """``read`` for a request into a standard diagnostic session, else ``actuate``.

    UDS is judged without the suppress-positive-response bit; KWP2000's 0x81-0x83 are its
    standard sessions. An unknown protocol must be read on both, and a request with no
    session type is not judged at all.
    """
    if len(data) < 2:
        return ACTUATE
    uds = (data[1] & 0x7F) in _UDS_READ_SESSION_TYPES
    kwp = data[1] in _KWP_READ_SESSION_TYPES
    reads = {UDS: uds, KWP: kwp}.get(protocol, uds and kwp)
    return READ if reads else ACTUATE


def default_release(request: str | bytes, protocol: DiagProtocol, mask_length: int = 0) -> str:
    """The release implied by an IOControl request, or ``""`` when none is.

    A UDS ``0x2F`` request is released by returnControlToECU on the same DID
    (``2F {DID} 00``), followed by a controlEnableMaskRecord of ``mask_length`` bytes of
    ``FF`` when the DID carries one: all ones hands every parameter back (ISO 14229-1
    12.2.1). A KWP2000 ``0x30`` request has no implied release, and neither has text
    that is not a hex request.
    """
    try:
        io = parse_io_request(request)
    except ValueError:
        return ""
    if io is None or io.service != INPUT_OUTPUT_CONTROL_BY_IDENTIFIER or protocol == KWP:
        return ""
    did = int.from_bytes(io.identifier, "big")
    return io_control_request(did, _UDS_RETURN_CONTROL, mask=b"\xff" * mask_length)


def resolve_release(
    declared: object,
    on: str,
    off: str,
    protocol: DiagProtocol,
    key: str = "",
    enable_mask: str = "",
) -> str:
    """An IOControl entry's release: the declared ``release:``, else the one it implies.

    ``on``/``off`` imply one when they are UDS ``0x2F`` requests. An entry with neither
    (driven only by the value keys, which build ``2F {key} 03 …``) implies the release of
    its own 4-digit DID key. ``enable_mask`` is the entry's controlEnableMaskRecord (hex),
    whose length the implied release's mask takes.
    """
    if declared not in (None, ""):
        if not isinstance(declared, str):
            raise ValueError(f"release must be a quoted hex request, got {declared!r}")
        return normalize_request(declared)
    mask_length = len(request_bytes(enable_mask)) if enable_mask else 0
    if not on and not off and len(key) == 4 and all(c in "0123456789ABCDEF" for c in key):
        on = f"2F{key}00"
    for request in (on, off):
        if request:
            implied = default_release(request, protocol, mask_length)
            if implied:
                return implied
    return ""


def release_mismatch(on: str, release: str) -> str | None:
    """Why ``release`` cannot release ``on``, or None when it can.

    A release has to address the same output: an IOControl request on the same
    service and identifier, or, for a KWP2000 ``0x30`` output, StopDiagnosticSession
    (``20``), which hands every driven output back. Anything else (a TesterPresent,
    an ECU reset) would leave the output driven, and a release the blocklist refuses
    could never be sent at all.
    """
    if normalize_request(on) == normalize_request(release):
        return f"release {normalize_request(release)} is the ON request itself"
    blocked = check_command_safety(release)
    if blocked is not None:
        return f"release {normalize_request(release)} could never be sent: {blocked}"
    on_io, rel_io = parse_io_request(on), parse_io_request(release)
    if on_io is None:
        return None
    if rel_io is None:
        if (
            on_io.service == KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER
            and request_bytes(release) == _KWP_STOP_SESSION
        ):
            return None
        return (
            f"release {normalize_request(release)} is not an IOControl request on "
            f"{on_io.service:02X} {on_io.identifier.hex().upper()}"
            + (
                " or StopDiagnosticSession (20)"
                if on_io.service == KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER
                else ""
            )
            + ", so it would not hand the output back"
        )
    if (on_io.service, on_io.identifier) != (rel_io.service, rel_io.identifier):
        return (
            f"release {normalize_request(release)} addresses "
            f"{rel_io.service:02X} {rel_io.identifier.hex().upper()}, but the ON request "
            f"addresses {on_io.service:02X} {on_io.identifier.hex().upper()}"
        )
    return None


def on_refusal(on: str, release: str, protocol: DiagProtocol) -> str | None:
    """Why ``canair io`` must not send the ON request ``on``, or None to allow it.

    An actuating request is only sent when canair knows how to hand control back
    afterwards (on exit, Ctrl-C, or a dropped session), so that a toggled output is
    never left driven. ``release`` is the resolved release (declared or implied).
    """
    if not on:
        return None
    try:
        return _on_refusal(on, release, protocol)
    except ValueError as exc:
        return str(exc)


def _on_refusal(on: str, release: str, protocol: DiagProtocol) -> str | None:
    # Judged without the declared release: declaring the ON request itself as its
    # release must not make it pass.
    if classify(on, protocol) != ACTUATE:
        return None
    if release:
        return release_mismatch(on, release)
    io = parse_io_request(on)
    what = (
        "a KWP2000 0x30 request, whose option byte has no standard release"
        if io is not None and io.service == KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER
        else "a request with no implied release"
    )
    return (
        f"no release declared: {normalize_request(on)} is {what}. Find the request "
        "that hands control back and set it with `canair pids set-iocontrol ECU KEY "
        "--release HEX`"
    )
