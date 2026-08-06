"""Byte-role layout of a UDS/KWP2000 response: which bytes are header, and what each is.

A response payload is ``SID`` then a service-specific **header** then data. The
header is *not* a single width: service ``0x22`` echoes a 2-byte DID, ``0x21`` a
1-byte local identifier, ``0x31`` puts a sub-function byte *before* its 2-byte
routine id, ``0x2F`` puts a control parameter *after* its DID, and a negative
response (``0x7F``) carries the rejected service's SID plus a response code. This
module is the one table that says so, so a byte can be *named* (``DID`` / ``LID`` /
``RID`` / ``NRC`` / …) instead of guessed at from a 1-vs-2-byte width flag.

It builds on :mod:`canlib.uds_services` (the SID registry) and reuses
:data:`canlib.uds_parse.NRC_CODES` for response-code names; the roles use the same
``DID``/``LID``/``RID`` vocabulary as :class:`canlib.modes.discovery_scan.DiscoveryProbe`.
Data-only plus small resolvers: no I/O, no formatting decisions (a caller renders
the labels and picks which definitions to show).

The layouts are grounded in this repo's existing per-service knowledge — chiefly
:func:`canlib.formatting.decode_uds_response`, :func:`canlib.uds_parse.request_echo`,
and the scanner docstrings in :mod:`canlib.modes.kwp_routines_scan` /
:mod:`canlib.modes.kwp_iocontrol_scan` — and a service is deliberately **omitted**
rather than guessed when its layout isn't established, so callers fall back to
their previous behaviour instead of asserting a wrong one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .uds_parse import NRC_CODES
from .uds_services import NEGATIVE_RESPONSE_SID, RESPONSE_SID_OFFSET, service_info

# ── role vocabulary ────────────────────────────────────────────────────────
# Short labels for a Role column. PCI is produced by the ISO-TP frame walker
# (framing lives below UDS), the rest by this module.
ROLE_PCI = "PCI"
ROLE_SID = "SID"
ROLE_SF = "SF"
ROLE_DID = "DID"
ROLE_LID = "LID"
ROLE_PID = "PID"
ROLE_RID = "RID"
ROLE_REC = "REC"
ROLE_INFO = "INFO"
ROLE_FRAME = "FRAME"
ROLE_CTRL = "CTRL"
ROLE_REJ_SID = "REJ SID"
ROLE_NRC = "NRC"

#: Plain-language definition of every role, for a legend/definition list.
ROLE_HELP: dict[str, str] = {
    ROLE_PCI: "ISO-TP framing byte (First/Consecutive Frame header) — never data",
    ROLE_SID: "Service Identifier — the response service byte (request SID + 0x40)",
    ROLE_SF: "sub-function byte — selects the mode within the service",
    ROLE_DID: "Data Identifier — 2-byte UDS identifier (services 0x22 / 0x2E / 0x2F)",
    ROLE_LID: (
        "Local Identifier — 1-byte KWP2000 identifier (0x21 / 0x30 / 0x33); "
        'canair also writes these as 21xx "PIDs"'
    ),
    ROLE_PID: "Parameter ID — 1-byte OBD-II parameter number (modes 0x01 / 0x02)",
    ROLE_RID: "Routine Identifier — 2-byte UDS routine id (service 0x31)",
    ROLE_REC: "Record number — 1-byte KWP2000 ReadEcuIdentification record (0x1A)",
    ROLE_INFO: "InfoType — 1-byte OBD-II mode 0x09 vehicle-information id",
    ROLE_FRAME: "freeze-frame number (OBD-II mode 0x02)",
    ROLE_CTRL: "inputOutputControlParameter — what the ECU was told to do (0x2F / 0x30)",
    ROLE_REJ_SID: "the rejected service's SID, echoed back in a negative response",
    ROLE_NRC: "Negative Response Code — why the request was refused",
}


@dataclass(frozen=True)
class HeaderField:
    """One labelled header field sitting after the response SID."""

    role: str
    width: int


@dataclass(frozen=True)
class ResponseLayout:
    """The header layout of one response SID.

    ``fields`` are the labelled bytes **after** the SID, in wire order — so
    ``0x71`` (RoutineControl) is ``(SF, RID)`` and ``0x2F``'s response ``0x6F`` is
    ``(DID, CTRL)``. Everything past the header is data.
    """

    resp_sid: int
    fields: tuple[HeaderField, ...]
    request_sid: int | None = None
    negative: bool = False

    @property
    def header_bytes(self) -> int:
        """Total header size in bytes, SID included."""
        return 1 + sum(f.width for f in self.fields)

    @property
    def subfunction_bytes(self) -> int:
        """Header size *excluding* the SID.

        This is the generalisation of the ``-1``/``-2`` subfunction width: Torque
        and OBDb bix count from the first UDS *data* byte, which is
        ``1 + subfunction_bytes`` into the payload whatever the header's shape (3
        for ``0x71``'s ``SF + RID``, not the 1 or 2 a width flag can express).
        """
        return self.header_bytes - 1

    def role_at(self, isotp_idx: int) -> str | None:
        """Role of ISO-TP payload byte ``isotp_idx``, or ``None`` if it is data."""
        if isotp_idx < 0:
            return None
        if isotp_idx == 0:
            return ROLE_SID
        pos = 1
        for field in self.fields:
            if isotp_idx < pos + field.width:
                return field.role
            pos += field.width
        return None

    def roles(self) -> list[str]:
        """Every role this layout can label, in wire order (SID first)."""
        return [ROLE_SID, *(f.role for f in self.fields)]


def _f(role: str, width: int = 1) -> HeaderField:
    return HeaderField(role, width)


# Header fields per **response** SID (what is actually in a payload).
#
# Only layouts established elsewhere in this repo (or unambiguous in ISO
# 14229 / SAE J1979) are listed. An absent service resolves to None so the caller
# keeps its previous behaviour rather than asserting a guessed layout.
_RESPONSE_FIELDS: dict[int, tuple[HeaderField, ...]] = {
    0x41: (_f(ROLE_PID),),  # 0x01 OBD-II show current data
    0x42: (_f(ROLE_PID), _f(ROLE_FRAME)),  # 0x02 freeze-frame data
    0x49: (_f(ROLE_INFO),),  # 0x09 vehicle information
    0x50: (_f(ROLE_SF),),  # 0x10 DiagnosticSessionControl — session type
    0x51: (_f(ROLE_SF),),  # 0x11 ECUReset — reset type
    0x59: (_f(ROLE_SF),),  # 0x19 ReadDTCInformation — report type
    0x5A: (_f(ROLE_REC),),  # 0x1A ReadEcuIdentification — record number
    0x61: (_f(ROLE_LID),),  # 0x21 ReadDataByLocalIdentifier
    0x62: (_f(ROLE_DID, 2),),  # 0x22 ReadDataByIdentifier
    0x67: (_f(ROLE_SF),),  # 0x27 SecurityAccess — level
    0x6E: (_f(ROLE_DID, 2),),  # 0x2E WriteDataByIdentifier
    0x6F: (_f(ROLE_DID, 2), _f(ROLE_CTRL)),  # 0x2F IOControlByIdentifier
    0x70: (_f(ROLE_LID), _f(ROLE_CTRL)),  # 0x30 IOControlByLocalIdentifier
    0x71: (_f(ROLE_SF), _f(ROLE_RID, 2)),  # 0x31 RoutineControl — SF BEFORE the RID
    0x72: (_f(ROLE_LID),),  # 0x32 StopRoutineByLocalIdentifier
    0x73: (_f(ROLE_LID),),  # 0x33 RequestRoutineResultsByLocalIdentifier
    0x7B: (_f(ROLE_LID),),  # 0x3B WriteDataByLocalIdentifier
    0x7E: (_f(ROLE_SF),),  # 0x3E TesterPresent — zeroSubFunction echo
}

#: A negative response is ``7F <rejected SID> <NRC>`` regardless of service.
_NEGATIVE_FIELDS: tuple[HeaderField, ...] = (_f(ROLE_REJ_SID), _f(ROLE_NRC))


def response_layout(resp_sid: int) -> ResponseLayout | None:
    """The header layout for response SID ``resp_sid``, or ``None`` if unknown.

    ``0x7F`` resolves to the service-independent negative-response layout. For a
    positive response the explicit table wins; failing that, a service the
    :mod:`canlib.uds_services` registry gives an ``id_width`` for resolves to a
    single identifier field (2 bytes → ``DID``, 1 byte → ``LID``), so a plain
    identifier read is covered without being listed twice.
    """
    if resp_sid == NEGATIVE_RESPONSE_SID:
        return ResponseLayout(resp_sid, _NEGATIVE_FIELDS, negative=True)
    request_sid = (resp_sid - RESPONSE_SID_OFFSET) & 0xFF
    fields = _RESPONSE_FIELDS.get(resp_sid)
    if fields is None:
        info = service_info(request_sid)
        if info is None or info.id_width <= 0:
            return None
        fields = (_f(ROLE_DID if info.id_width == 2 else ROLE_LID, info.id_width),)
    return ResponseLayout(resp_sid, fields, request_sid=request_sid)


def nrc_name(nrc: int) -> str:
    """Human name for a negative response code (``requestOutOfRange``, …)."""
    return NRC_CODES.get(nrc, f"unknown (0x{nrc:02X})")


def role_definitions(roles: Iterable[str]) -> list[tuple[str, str]]:
    """``(role, definition)`` pairs for ``roles``, deduped and in vocabulary order.

    Ordered by :data:`ROLE_HELP` (protocol layering: framing, then the UDS header
    fields) rather than by the caller's iteration order, so a definition list
    reads the same whatever payload produced it. Unknown roles are skipped.
    """
    wanted = set(roles)
    return [(role, help_) for role, help_ in ROLE_HELP.items() if role in wanted]
