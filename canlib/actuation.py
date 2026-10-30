"""What a diagnostic request does to the car: ``read``, ``release`` or ``actuate``.

The request classifier from ``plans/2026-10-22-actuation-safety-interlock.md`` §1:
an allowlist, so a service nobody has classified fails closed as ``actuate``.
Service facts (names, identifier widths) stay in :mod:`canlib.uds.services`; this
module only adds what a request *does*.

KWP2000 ``InputOutputControlByLocalIdentifier`` (``0x30``) has no standard release.
ISO 14230-3 leaves its control-option byte to the manufacturer, and the byte that
SSF 14230-3 calls returnControlToECU (``00``) actuates elsewhere: Nissan's ``00`` is
a one-shot command, and the OVMS Kia Soul EV firmware sends ``30 {led} 00`` to
switch an on-board charger LED *on*. So a ``0x30`` request is a release only when
the profile declares it as one (an IOControl entry's ``release:``), and every other
``0x30`` request is ``actuate``.

Pure and transport-free. Nothing calls it from the transports yet: the interlock's
funnel is still a plan. ``canair io`` uses it to refuse an ON request that has no
known release (:func:`on_refusal`).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Final, Literal

from .safety import KWP, UDS, SessionProtocol
from .uds.services import service_info

RequestClass = Literal["read", "release", "actuate"]
READ: Final[RequestClass] = "read"
RELEASE: Final[RequestClass] = "release"
ACTUATE: Final[RequestClass] = "actuate"

IOCONTROL_UDS = 0x2F
IOCONTROL_KWP = 0x30

# UDS InputOutputControlParameter (ISO 14229-1): 00 returnControlToECU. 01-03
# (resetToDefault, freezeCurrentState, shortTermAdjustment) drive the output.
UDS_RETURN_CONTROL = 0x00

# Services that only read, or only move the diagnostic session, on every protocol:
# the OBD-II read modes, session control, DTC and data reads, SecurityAccess and
# TesterPresent. 0x04 (OBD-II clear) and 0x14 (clear diagnostic information) are
# deliberately absent: clearing DTCs changes ECU state.
_READ_SERVICES: Final = frozenset(
    {
        0x01,
        0x02,
        0x03,
        0x07,
        0x09,
        0x0A,
        0x10,
        0x12,
        0x13,
        0x17,
        0x18,
        0x19,
        0x1A,
        0x21,
        0x22,
        0x23,
        0x27,
        0x3E,
    }
)

# KWP2000 services that read or hand control back, judged on a KWP2000 ECU (and,
# being KWP2000-only bytes, on an ECU of unknown protocol too).
_KWP_READ: Final = frozenset({0x33, 0x3A})  # routine results by LID / by address
# StopDiagnosticSession ends the session, and ISO 14230-3 has the ECU release every
# actuator the tester drove; StopRoutineByLocalIdentifier stops a started routine.
_KWP_RELEASE: Final = frozenset({0x20, 0x32})

# UDS RoutineControl (0x31) sub-functions. On KWP2000 the same byte is
# StartRoutineByLocalIdentifier, which actuates whatever follows it.
_UDS_ROUTINE_STOP = 0x02
_UDS_ROUTINE_RESULTS = 0x03


@dataclass(frozen=True)
class IoRequest:
    """An IOControl request split into its fields.

    UDS ``0x2F``: ``2F {DID_HI DID_LO} {option} [state…]``. KWP2000 ``0x30``:
    ``30 {LID} {option} [state…]`` with an 8-bit local identifier (LID), whose
    ``option`` byte means whatever its manufacturer decided. ``option`` is None
    when the request stops after the identifier.
    """

    service: int
    identifier: bytes
    option: int | None
    state: bytes


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


def parse_io_request(request: str | bytes) -> IoRequest | None:
    """Split an IOControl request (``0x2F`` or ``0x30``), or None for anything else.

    Returns None when the request is too short to carry its whole identifier.
    """
    data = request_bytes(request)
    if not data or data[0] not in (IOCONTROL_UDS, IOCONTROL_KWP):
        return None
    info = service_info(data[0])
    width = info.id_width if info else 0
    if len(data) < 1 + width:
        return None
    rest = data[1 + width :]
    return IoRequest(
        service=data[0],
        identifier=data[1 : 1 + width],
        option=rest[0] if rest else None,
        state=rest[1:],
    )


def classify(
    request: str | bytes,
    protocol: SessionProtocol,
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
    if sid == IOCONTROL_UDS:
        if protocol != KWP and len(data) == 4 and data[3] == UDS_RETURN_CONTROL:
            return RELEASE
        return ACTUATE
    if sid == 0x31:
        if protocol == UDS and len(data) >= 2:
            if data[1] == _UDS_ROUTINE_RESULTS:
                return READ
            if data[1] == _UDS_ROUTINE_STOP:
                return RELEASE
        return ACTUATE
    if protocol != UDS:
        if sid in _KWP_READ:
            return READ
        if sid in _KWP_RELEASE:
            return RELEASE
    return ACTUATE


def default_release(request: str | bytes, protocol: SessionProtocol) -> str:
    """The release implied by an IOControl request, or ``""`` when none is.

    A UDS ``0x2F`` request is released by returnControlToECU on the same DID
    (``2F {DID} 00``). A KWP2000 ``0x30`` request has no implied release, and
    neither has text that is not a hex request.
    """
    try:
        io = parse_io_request(request)
    except ValueError:
        return ""
    if io is None or io.service != IOCONTROL_UDS or protocol == KWP:
        return ""
    return f"2F{io.identifier.hex().upper()}{UDS_RETURN_CONTROL:02X}"


def release_mismatch(on: str, release: str) -> str | None:
    """Why ``release`` cannot release ``on``, or None when it can.

    A release has to address the same output: the same service and identifier.
    """
    if normalize_request(on) == normalize_request(release):
        return f"release {normalize_request(release)} is the ON request itself"
    on_io, rel_io = parse_io_request(on), parse_io_request(release)
    if on_io is None or rel_io is None:
        return None
    if (on_io.service, on_io.identifier) != (rel_io.service, rel_io.identifier):
        return (
            f"release {normalize_request(release)} addresses "
            f"{rel_io.service:02X} {rel_io.identifier.hex().upper()}, but the ON request "
            f"addresses {on_io.service:02X} {on_io.identifier.hex().upper()}"
        )
    return None


def on_refusal(on: str, release: str, protocol: SessionProtocol) -> str | None:
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


def _on_refusal(on: str, release: str, protocol: SessionProtocol) -> str | None:
    # Judged without the declared release: declaring the ON request itself as its
    # release must not make it pass.
    if classify(on, protocol) != ACTUATE:
        return None
    if release:
        return release_mismatch(on, release)
    io = parse_io_request(on)
    what = (
        "a KWP2000 0x30 request, whose option byte has no standard release"
        if io is not None and io.service == IOCONTROL_KWP
        else "a request with no implied release"
    )
    return (
        f"no release declared: {normalize_request(on)} is {what}. Find the request "
        "that hands control back and set it with `canair pids set-iocontrol ECU KEY "
        "--release HEX`"
    )
