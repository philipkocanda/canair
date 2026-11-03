"""Negative Response Code (NRC) names — ISO 14229-1 (UDS) and SSF 14230-3 (KWP2000).

A negative response is ``7F <service> <code>``. Most codes mean the same on both
protocols, but some do not (``0x72`` is generalProgrammingFailure on UDS and
transferAborted on KWP2000), and the 0x80-0xFF range is laid out differently. So a
name is looked up for the target ECU's :data:`~canlib.uds.protocol.DiagProtocol`,
and :func:`nrc_description` and :func:`nrc_abbrev` share one resolver, so the long
name and the mnemonic of a code can never disagree.
"""

from __future__ import annotations

from typing import Final

from canlib.uds.protocol import KWP, UDS, DiagProtocol
from canlib.uds.services import NEGATIVE_RESPONSE_SID

#: requestCorrectlyReceivedResponsePending: an interim "still working" answer, the same
#: code on UDS and KWP2000. The ECU sends its real answer later.
NRC_RESPONSE_PENDING: Final = 0x78


def is_response_pending(reply: bytes, sid: int | None = None) -> bool:
    """True when ``reply`` is a ResponsePending (``7F <sid> 78``), optionally for ``sid``."""
    return (
        len(reply) >= 3
        and reply[0] == NEGATIVE_RESPONSE_SID
        and reply[2] == NRC_RESPONSE_PENDING
        and (sid is None or reply[1] == sid)
    )


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
    # ISO 14229-1 conditionsNotCorrect refinements: why the ECU refused *now*.
    0x81: "rpmTooHigh",
    0x82: "rpmTooLow",
    0x83: "engineIsRunning",
    0x84: "engineIsNotRunning",
    0x85: "engineRunTimeTooLow",
    0x86: "temperatureTooHigh",
    0x87: "temperatureTooLow",
    0x88: "vehicleSpeedTooHigh",
    0x89: "vehicleSpeedTooLow",
    0x8A: "throttle/PedalTooHigh",
    0x8B: "throttle/PedalTooLow",
    0x8C: "transmissionRangeNotInNeutral",
    0x8D: "transmissionRangeNotInGear",
    0x8F: "brakeSwitch(es)NotClosed",
    0x90: "shifterLeverNotInPark",
    0x91: "torqueConverterClutchLocked",
    0x92: "voltageTooHigh",
    0x93: "voltageTooLow",
}

# Short mnemonics for compact UI display (TUI "Last response" column, etc), one per
# NRC_CODES entry and derived from the initials of its name.
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
    # ISO 14229-1's own mnemonics for the conditionsNotCorrect refinements.
    0x81: "RPMTH",
    0x82: "RPMTL",
    0x83: "EIR",
    0x84: "EINR",
    0x85: "ERTTL",
    0x86: "TEMPTH",
    0x87: "TEMPTL",
    0x88: "VSTH",
    0x89: "VSTL",
    0x8A: "TPTH",
    0x8B: "TPTL",
    0x8C: "TRNIN",
    0x8D: "TRNIG",
    0x8F: "BSNC",
    0x90: "SLNIP",
    0x91: "TCCL",
    0x92: "VTH",
    0x93: "VTL",
}

# ISO 14229-1 ranges without per-code names: (codes, name, mnemonic). 0xF0-0xFE differ
# per make (Hyundai/Kia use them for gateway authentication and downgrade refusals), so
# they get the range name only.
_NRC_RANGES = (
    (range(0x94, 0xF0), "reservedForSpecificConditionsNotCorrect", "RFSCNC"),
    (range(0xF0, 0xFF), "vehicleManufacturerSpecificConditionsNotCorrect", "VMSCNC"),
)

# KWP2000 response codes that are absent from, or mean something other than, their ISO 14229-1
# namesake. Names and ranges follow SSF 14230-3 Issue 2 section 4.4, the implementation standard
# the KWP2000 scanners already cite; the ISO 14230-3 draft leaves 0x80-0xFF entirely to the
# manufacturer. The codes both protocols define alike are _KWP_SHARED_CODES.
KWP_NRC_CODES = {
    0x12: "subFunctionNotSupportedOrInvalidFormat",  # UDS splits this into 0x12 + 0x13
    0x22: "conditionsNotCorrectOrRequestSequenceError",  # UDS splits this into 0x22 + 0x24
    # Still running, not refused: repeating the request must not restart it, and the ECU then
    # answers 0x21 busyRepeatRequest until it finishes. Reserved in UDS.
    0x23: "routineNotCompleteOrServiceInProgress",
    0x40: "downloadNotAccepted",
    0x41: "improperDownloadType",
    0x42: "cantDownloadToSpecifiedAddress",
    0x43: "cantDownloadNumberOfBytesRequested",
    0x50: "uploadNotAccepted",
    0x51: "improperUploadType",
    0x52: "cantUploadFromSpecifiedAddress",
    0x53: "cantUploadNumberOfBytesRequested",
    0x72: "transferAborted",  # UDS 0x72 is generalProgrammingFailure
    0x74: "illegalAddressInBlockTransfer",
    0x75: "illegalByteCountInBlockTransfer",
    0x76: "illegalBlockTransferType",
    0x77: "blockTransferDataChecksumError",
    0x79: "incorrectByteCountDuringBlockTransfer",
    0x80: "serviceNotSupportedInActiveDiagnosticSession",  # UDS: 0x7F
}

# Mnemonics for KWP_NRC_CODES, as SSF 14230-3 Issue 2 section 4.4 spells them.
_KWP_ABBREV = {
    0x12: "SFNSIF",  # subFunctionNotSupportedOrInvalidFormat
    0x22: "CNCORSE",  # conditionsNotCorrectOrRequestSequenceError
    0x23: "RNC",  # routineNotCompleteOrServiceInProgress
    0x40: "DNA",  # downloadNotAccepted
    0x41: "IDT",  # improperDownloadType
    0x42: "CNDTSA",  # cantDownloadToSpecifiedAddress
    0x43: "CNDNOBR",  # cantDownloadNumberOfBytesRequested
    0x50: "UNA",  # uploadNotAccepted
    0x51: "IUT",  # improperUploadType
    0x52: "CNUFSA",  # cantUploadFromSpecifiedAddress
    0x53: "CNUNOBR",  # cantUploadNumberOfBytesRequested
    0x72: "TA",  # transferAborted
    0x74: "IAIBT",  # illegalAddressInBlockTransfer
    0x75: "IBCIBT",  # illegalByteCountInBlockTransfer
    0x76: "IBTT",  # illegalBlockTransferType
    0x77: "BTDCE",  # blockTransferDataChecksumError
    0x79: "IBCDBT",  # incorrectByteCountDuringBlockTransfer
    0x80: "SNSIADS",  # serviceNotSupportedInActiveDiagnosticSession
}

# The ISO 14229-1 codes KWP2000 defines with the same meaning, so a KWP lookup reads their
# NRC_CODES name. Every other code ISO 14229-1 names below 0x80 (0x13, 0x14, 0x24-0x26, 0x70,
# 0x73, 0x7E, 0x7F) is not a KWP2000 code and must not be read as one.
_KWP_SHARED_CODES = frozenset({0x10, 0x11, 0x21, 0x31, 0x33, 0x35, 0x36, 0x37, 0x71, 0x78})

# KWP2000 range names for every code neither table names. ISO 14229-1's 0x81-0x93
# conditionsNotCorrect refinements (rpmTooHigh, ...) are not KWP2000 codes either.
_KWP_NRC_RANGES = (
    (range(0x00, 0x80), "reservedByDocument", "RBD"),
    (range(0x81, 0x90), "reservedByDocument", "RBD"),
    (range(0x90, 0xFA), "vehicleManufacturerSpecific", "VMS"),
    (range(0xFA, 0xFF), "systemSupplierSpecific", "SSS"),
)


def _resolve(nrc: int, protocol: DiagProtocol | None) -> tuple[str, str, bool] | None:
    """``(name, mnemonic, borrowed)`` for a code, or None when no table names it.

    ``borrowed`` marks a KWP2000 name offered for an ECU whose protocol is unknown.
    """
    if protocol == KWP:
        if nrc in KWP_NRC_CODES:
            return KWP_NRC_CODES[nrc], _KWP_ABBREV[nrc], False
        if nrc in _KWP_SHARED_CODES:
            return NRC_CODES[nrc], NRC_ABBREV[nrc], False
        for codes, name, abbrev in _KWP_NRC_RANGES:
            if nrc in codes:
                return name, abbrev, False
        return None
    if nrc in NRC_CODES:
        return NRC_CODES[nrc], NRC_ABBREV[nrc], False
    for codes, name, abbrev in _NRC_RANGES:
        if nrc in codes:
            return name, abbrev, False
    if protocol != UDS and nrc in KWP_NRC_CODES:
        return KWP_NRC_CODES[nrc], _KWP_ABBREV[nrc], True
    return None


def nrc_description(nrc: int, protocol: DiagProtocol | None = None) -> str:
    """Human name for a negative response code, or ``unknown (0xNN)``.

    ``protocol`` is the target ECU's diagnostic protocol (``"uds"``/``"kwp"``), when
    known, because the same code can mean different things:

    * ``"kwp"`` reads SSF 14230-3 only (``0x23`` is ``routineNotCompleteOrServiceInProgress``,
      ``0x72`` ``transferAborted``, ``0x90``-``0xF9`` ``vehicleManufacturerSpecific``), using
      the shared name for a code both protocols define alike; a code only ISO 14229-1 names
      (``0x13``, ``0x7F``) is ``reservedByDocument`` there.
    * ``"uds"`` reads ISO 14229-1 only.
    * Anything else (unknown protocol) reads ISO 14229-1 and, for a code that standard
      leaves unnamed, the KWP2000 name tagged ``(KWP2000)``.
    """
    found = _resolve(nrc, protocol)
    if found is None:
        return f"unknown (0x{nrc:02X})"
    name, _abbrev, borrowed = found
    return f"{name} (KWP2000)" if borrowed else name


def nrc_abbrev(nrc: int, protocol: DiagProtocol | None = None) -> str:
    """Short mnemonic for an NRC on ``protocol`` (see :func:`nrc_description`), or ``?``."""
    found = _resolve(nrc, protocol)
    return found[1] if found else "?"
