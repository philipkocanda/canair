"""Sub-function and control-parameter values, by service: the names and the constants.

The byte after a service's SID often selects what the request does: a session type, a reset
type, a DTC report type, a routine control type, an I/O control parameter. This module is the
one home for those values. Code that sends or recognises one reads the constant here, and
display code names a value through :func:`subfunction_name`.

The constants are ``IntEnum`` members named after the standard (ISO 14229-1, or SSF 14230-3
for KWP2000), so a member compares and formats as its byte. ``SUBFUNCTION_NAMES`` stays a
literal table, because it is the data; tests/uds/test_uds_subfunctions.py pins every member
to the name the table gives its value.

Belongs: sub-function values and their names. Session types that only a make's quirk permits
(Hyundai/Kia ``0x90``, Nissan ``0xC0``) stay with the quirk in ``canair.safety.quirks``.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

#: Bit 7 of a UDS sub-function byte: suppressPosRspMsgIndicationBit (ISO 14229-1). The low
#: seven bits are the sub-function itself.
SUPPRESS_POSITIVE_RESPONSE: Final = 0x80


class SessionType(IntEnum):
    """DiagnosticSessionControl (``0x10``) sessionType, ISO 14229-1."""

    DEFAULT_SESSION = 0x01
    PROGRAMMING_SESSION = 0x02
    EXTENDED_DIAGNOSTIC_SESSION = 0x03
    SAFETY_SYSTEM_DIAGNOSTIC_SESSION = 0x04


#: The UDS session types ISO 14229-1 leaves to others: 0x40-0x5F vehicleManufacturerSpecific,
#: 0x60-0x7E systemSupplierSpecific.
OEM_SESSION_TYPES: Final = range(0x40, 0x7F)


class KwpSessionType(IntEnum):
    """DiagnosticSessionControl (``0x10``) diagnosticMode, SSF 14230-3."""

    STANDARD_DIAGNOSTIC_SESSION = 0x81
    PERIODIC_DIAGNOSTIC_SESSION = 0x82
    EXTENDED_DIAGNOSTIC_SESSION = 0x83
    PROGRAMMING_SESSION = 0x85


class EcuResetType(IntEnum):
    """ECUReset (``0x11``) resetType, ISO 14229-1.

    Only the first three restart the ECU. 0x04/0x05 change how it powers down, and 0x40-0x7E
    are vehicleManufacturerSpecific and systemSupplierSpecific.
    """

    HARD_RESET = 0x01
    KEY_OFF_ON_RESET = 0x02
    SOFT_RESET = 0x03
    ENABLE_RAPID_POWER_SHUT_DOWN = 0x04
    DISABLE_RAPID_POWER_SHUT_DOWN = 0x05


class DtcReportType(IntEnum):
    """ReadDTCInformation (``0x19``) reportType, ISO 14229-1."""

    REPORT_NUMBER_OF_DTC_BY_STATUS_MASK = 0x01
    REPORT_DTC_BY_STATUS_MASK = 0x02
    REPORT_DTC_SNAPSHOT_IDENTIFICATION = 0x03
    REPORT_DTC_SNAPSHOT_RECORD_BY_DTC_NUMBER = 0x04


class DtcFormatIdentifier(IntEnum):
    """The DTCFormatIdentifier a ``59 01`` reply carries, ISO 14229-1:2013 Table D.14.

    Not a sub-function: the byte says how to read every 3-byte DTC the ECU reports, and an
    ECU supports one. Only the two SAE J2012-DA formats have the ``P0123-45`` text form;
    ISO 14229-1's own format leaves the decoding to the maker.
    """

    SAE_J2012_DA_DTC_FORMAT_00 = 0x00  # ISO 15031-6
    ISO_14229_1_DTC_FORMAT = 0x01  # manufacturer-defined decoding
    SAE_J1939_73_DTC_FORMAT = 0x02
    ISO_11992_4_DTC_FORMAT = 0x03
    SAE_J2012_DA_DTC_FORMAT_04 = 0x04  # ISO 27145-2 (WWH-OBD)


#: The DTC formats canair shows as SAE J2012 text (``B2915-00``).
J2012_DTC_FORMATS: Final = frozenset(
    {DtcFormatIdentifier.SAE_J2012_DA_DTC_FORMAT_00, DtcFormatIdentifier.SAE_J2012_DA_DTC_FORMAT_04}
)


class DtcGroup(IntEnum):
    """ClearDiagnosticInformation (``0x14``) groupOfDTC values that mean every DTC.

    ISO 14229-1 has one, three bytes wide. KWP2000's group is two bytes: ISO 14230-3 (and the
    DaimlerChrysler KWP2000 spec, which makes it mandatory for ``14`` and ``18``) uses FF00, the
    group canair's ``18 00 FF 00`` read sends too; SSF 14230-3 uses FFFF.
    """

    ALL_GROUPS = 0xFFFFFF
    KWP_ALL_GROUPS = 0xFF00
    KWP_ALL_DTCS_SSF = 0xFFFF


class IoControlParameter(IntEnum):
    """InputOutputControlByIdentifier (``0x2F``) inputOutputControlParameter, ISO 14229-1."""

    RETURN_CONTROL_TO_ECU = 0x00
    RESET_TO_DEFAULT = 0x01
    FREEZE_CURRENT_STATE = 0x02
    SHORT_TERM_ADJUSTMENT = 0x03


class KwpIoControlParameter(IntEnum):
    """InputOutputControlByLocalIdentifier (``0x30``) parameter, SSF 14230-3 Table 9.1.3.2.

    ISO 14230-3 leaves the byte to the manufacturer, and no make is known to follow SSF
    14230-3 here (see :mod:`canair.live.scan.kwp_iocontrol`). The values are named so a
    refusal can say what it refused. 0x09 (an I/O calibration report) is left unnamed until
    its name is checked against the standard; 0x0A-0xF9 are vehicleManufacturerSpecific and
    0xFA-0xFE systemSupplierSpecific.
    """

    RETURN_CONTROL_TO_ECU = 0x00
    REPORT_CURRENT_STATE = 0x01
    REPORT_IO_CONDITIONS = 0x02
    REPORT_IO_SCALING = 0x03
    RESET_TO_DEFAULT = 0x04  # actuates
    FREEZE_CURRENT_STATE = 0x05  # actuates
    EXECUTE_CONTROL_OPTION = 0x06  # actuates
    SHORT_TERM_ADJUSTMENT = 0x07  # actuates
    LONG_TERM_ADJUSTMENT = 0x08  # actuates and writes non-volatile memory


class RoutineControlType(IntEnum):
    """RoutineControl (``0x31``) routineControlType, ISO 14229-1."""

    START_ROUTINE = 0x01
    STOP_ROUTINE = 0x02
    REQUEST_ROUTINE_RESULTS = 0x03


class PeriodicTransmissionMode(IntEnum):
    """ReadDataByPeriodicIdentifier (``0x2A``) transmissionMode, ISO 14229-1.

    Not a sub-function: the byte has no suppress-positive-response bit.
    """

    SEND_AT_SLOW_RATE = 0x01
    SEND_AT_MEDIUM_RATE = 0x02
    SEND_AT_FAST_RATE = 0x03
    STOP_SENDING = 0x04


class TimingParameterAccessType(IntEnum):
    """AccessTimingParameter (``0x83``) timingParameterAccessType, ISO 14229-1."""

    READ_EXTENDED_TIMING_PARAMETER_SET = 0x01
    SET_TIMING_PARAMETERS_TO_DEFAULT_VALUES = 0x02
    READ_CURRENTLY_ACTIVE_TIMING_PARAMETERS = 0x03
    SET_TIMING_PARAMETERS_TO_GIVEN_VALUES = 0x04


class DtcSettingType(IntEnum):
    """ControlDTCSetting (``0x85``) DTCSettingType, ISO 14229-1 (ISO 14230-3 uses the same)."""

    ON = 0x01
    OFF = 0x02


class ResponseOnEventType(IntEnum):
    """ResponseOnEvent (``0x86``) eventType, ISO 14229-1.

    Bit 6 of the byte is storageState (store the event across a power cycle), so a value
    with it set is a different request.
    """

    STOP_RESPONSE_ON_EVENT = 0x00
    ON_DTC_STATUS_CHANGE = 0x01
    ON_TIMER_INTERRUPT = 0x02
    ON_CHANGE_OF_DATA_IDENTIFIER = 0x03
    REPORT_ACTIVATED_EVENTS = 0x04
    START_RESPONSE_ON_EVENT = 0x05
    CLEAR_RESPONSE_ON_EVENT = 0x06
    ON_COMPARISON_OF_VALUES = 0x07


# Named values of each service's sub-function / control byte, keyed by **request**
# SID. One home for these enums: they were previously re-declared per consumer
# (a display table in ``formatting.py``, scan constants in ``live/*_scan.py``),
# which is how ``0x28``'s controlType names ended up shifted a slot and filed
# under the wrong service. Unlisted values are rendered as hex by callers, so an
# incomplete table degrades rather than lies.
SUBFUNCTION_NAMES: dict[int, dict[int, str]] = {
    # DiagnosticSessionControl — sessionType. Includes the KWP2000 0x8x range;
    # omitting it made a Hyundai/Kia session response print bare hex.
    0x10: {
        0x01: "defaultSession",
        0x02: "programmingSession",
        0x03: "extendedDiagnosticSession",
        0x04: "safetySystemDiagnosticSession",
        0x81: "standardDiagnosticSession",
        0x82: "periodicDiagnosticSession",
        0x83: "extendedDiagnosticSession",
        0x85: "programmingSession",
    },
    # ECUReset — resetType.
    0x11: {
        0x01: "hardReset",
        0x02: "keyOffOnReset",
        0x03: "softReset",
        0x04: "enableRapidPowerShutDown",
        0x05: "disableRapidPowerShutDown",
    },
    # ReadDTCInformation — reportType (contiguous 0x01-0x0E plus the two counters).
    0x19: {
        0x01: "reportNumberOfDTCByStatusMask",
        0x02: "reportDTCByStatusMask",
        0x03: "reportDTCSnapshotIdentification",
        0x04: "reportDTCSnapshotRecordByDTCNumber",
        0x05: "reportDTCStoredDataByRecordNumber",
        0x06: "reportDTCExtDataRecordByDTCNumber",
        0x07: "reportNumberOfDTCBySeverityMaskRecord",
        0x08: "reportDTCBySeverityMaskRecord",
        0x09: "reportSeverityInformationOfDTC",
        0x0A: "reportSupportedDTC",
        0x0B: "reportFirstTestFailedDTC",
        0x0C: "reportFirstConfirmedDTC",
        0x0D: "reportMostRecentTestFailedDTC",
        0x0E: "reportMostRecentConfirmedDTC",
        0x14: "reportDTCFaultDetectionCounter",
        0x15: "reportDTCWithPermanentStatus",
    },
    # CommunicationControl — controlType. 0x00 IS a valid value.
    0x28: {
        0x00: "enableRxAndTx",
        0x01: "enableRxAndDisableTx",
        0x02: "disableRxAndEnableTx",
        0x03: "disableRxAndTx",
        0x04: "enableRxAndDisableTxWithEnhancedAddressInformation",
        0x05: "enableRxAndTxWithEnhancedAddressInformation",
    },
    # ReadDataByPeriodicIdentifier — transmissionMode.
    0x2A: {
        0x01: "sendAtSlowRate",
        0x02: "sendAtMediumRate",
        0x03: "sendAtFastRate",
        0x04: "stopSending",
    },
    # DynamicallyDefineDataIdentifier — definitionType.
    0x2C: {
        0x01: "defineByIdentifier",
        0x02: "defineByMemoryAddress",
        0x03: "clearDynamicallyDefinedDataIdentifier",
    },
    # InputOutputControlByIdentifier — inputOutputControlParameter. Mirrored by
    # the KWP2000 0x30 entry below (protocol-shared values).
    0x2F: {
        0x00: "returnControlToECU",
        0x01: "resetToDefault",
        0x02: "freezeCurrentState",
        0x03: "shortTermAdjustment",
    },
    # RoutineControl — routineControlType.
    0x31: {
        0x01: "startRoutine",
        0x02: "stopRoutine",
        0x03: "requestRoutineResults",
    },
    # AccessTimingParameter — timingParameterAccessType.
    0x83: {
        0x01: "readExtendedTimingParameterSet",
        0x02: "setTimingParametersToDefaultValues",
        0x03: "readCurrentlyActiveTimingParameters",
        0x04: "setTimingParametersToGivenValues",
    },
    # ControlDTCSetting — DTCSettingType.
    0x85: {0x01: "on", 0x02: "off"},
    # ResponseOnEvent — eventType, without the storageState bit (0x40).
    0x86: {
        0x00: "stopResponseOnEvent",
        0x01: "onDTCStatusChange",
        0x02: "onTimerInterrupt",
        0x03: "onChangeOfDataIdentifier",
        0x04: "reportActivatedEvents",
        0x05: "startResponseOnEvent",
        0x06: "clearResponseOnEvent",
        0x07: "onComparisonOfValues",
    },
}
# KWP2000 InputOutputControlByLocalIdentifier's control parameter, per SSF 14230-3. It shares
# returnControlToECU with UDS 0x2F, and no other value: SSF's 0x01 reportCurrentState is UDS
# 0x2F's resetToDefault.
SUBFUNCTION_NAMES[0x30] = {
    0x00: "returnControlToECU",
    0x01: "reportCurrentState",
    0x02: "reportIOConditions",
    0x03: "reportIOScaling",
    0x04: "resetToDefault",
    0x05: "freezeCurrentState",
    0x06: "executeControlOption",
    0x07: "shortTermAdjustment",
    0x08: "longTermAdjustment",
}
# TesterPresent's only sub-function, per ISO 14229-1; 0x80 is it with suppressPosRspMsgIndication.
SUBFUNCTION_NAMES[0x3E] = {
    0x00: "zeroSubFunction",
    0x80: "zeroSubFunction, suppressPosRspMsgIndication",
}


def subfunction_name(request_sid: int, value: int) -> str | None:
    """Named sub-function/control value for ``request_sid``, or ``None``.

    ``None`` means "not named here" — a caller should fall back to hex rather
    than invent a label.
    """
    return SUBFUNCTION_NAMES.get(request_sid, {}).get(value)


def parse_session_type(value: int | str) -> int:
    """A DiagnosticSessionControl sub-function, from its byte or its hex text.

    Profiles, steps and the CLI carry it as text (``"03"``, ``"0x3"``, ``"81"``); code that
    sends or compares it uses the byte. Raises ``ValueError`` for anything that is not one
    byte.
    """
    if isinstance(value, int):
        sub = value
    else:
        text = value.strip().upper().removeprefix("0X")
        if not text:
            raise ValueError("empty session type")
        sub = int(text, 16)
    if not 0 <= sub <= 0xFF:
        raise ValueError(f"session type 0x{sub:X} is not one byte")
    return sub


def format_session_type(value: int) -> str:
    """The two hex digits a session type is shown and stored as (``0x03`` → ``"03"``)."""
    return f"{value:02X}"
