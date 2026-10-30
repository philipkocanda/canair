"""Diagnostic service (SID) reference tables — UDS (ISO 14229) + KWP2000 (ISO 14230).

Data-only module (like ``identity_records.py``): the single source of truth for
*what a service byte means*. Naming, scan presets, the command safety blocklist,
and the discovery-scan probe configs all derive from here rather than each
re-declaring service facts.

A "service" here is the request SID (e.g. ``0x2F``). The positive response SID is
``SID + 0x40`` (e.g. ``0x6F``); ``0x7F`` is the negative-response marker.

Some SIDs are shared between UDS and KWP2000 (``0x10``, ``0x27``, ``0x31``, ``0x3E``,
…) while others are protocol-specific. Where a byte means different things in each
protocol we keep the entry that matches how *this* project's ECUs actually use it
(the Ioniq's powertrain ECUs are KWP2000, body/comfort ECUs are UDS).
"""

from __future__ import annotations

from dataclasses import dataclass

# Protocol/family tags.
UDS = "UDS"
KWP2000 = "KWP2000"
BOTH = "UDS/KWP2000"
OBD2 = "OBD-II"


@dataclass(frozen=True)
class ServiceInfo:
    """Metadata for one diagnostic service (request SID)."""

    sid: int
    name: str
    family: str  # UDS / KWP2000 / UDS/KWP2000
    # Identifier width in bytes for services that carry an id after the SID
    # (1 = KWP local identifier, 2 = UDS DID/RID). 0 = no id / not applicable.
    id_width: int = 0
    # The side-effect-free sub-function used to *discover* ids for this service
    # (e.g. 0x00 returnControlToECU, 0x03 requestRoutineResults). None if there
    # is no safe discovery sub-function.
    safe_discovery_sf: int | None = None
    # True if this service can actuate hardware / write memory (handle with care).
    actuates: bool = False


# Ordered registry. Kept in ascending SID order for readability.
SERVICES: tuple[ServiceInfo, ...] = (
    # --- SAE J1979 OBD-II modes ---
    # Legislated OBD-II modes share the request-SID byte position and the same
    # "+0x40 on a positive response" convention as UDS/KWP2000, in a disjoint
    # low range (0x01-0x0A vs 0x10+), so they belong in the same registry. Modes
    # 0x05/0x06/0x08 are deliberately absent: their identifiers are an O2-monitor
    # id / MID / TID rather than a PID, and modelling them as one would mislabel
    # the byte (an absent service falls back to the caller's default instead).
    ServiceInfo(0x01, "ShowCurrentData", OBD2, id_width=1),
    ServiceInfo(0x02, "ShowFreezeFrameData", OBD2, id_width=1),
    ServiceInfo(0x03, "ShowStoredDTCs", OBD2),
    ServiceInfo(0x04, "ClearDTCsAndStoredValues", OBD2, actuates=True),
    ServiceInfo(0x07, "ShowPendingDTCs", OBD2),
    ServiceInfo(0x09, "RequestVehicleInformation", OBD2, id_width=1),
    ServiceInfo(0x0A, "ShowPermanentDTCs", OBD2),
    # --- ISO 14230 KWP2000 diagnostic management / data ---
    ServiceInfo(0x10, "DiagnosticSessionControl", BOTH),
    ServiceInfo(0x11, "ECUReset", BOTH, actuates=True),
    ServiceInfo(0x12, "ReadFreezeFrameData (KWP2000)", KWP2000),
    ServiceInfo(0x13, "ReadDTCs (KWP2000)", KWP2000),
    ServiceInfo(0x14, "ClearDiagnosticInformation", BOTH),
    ServiceInfo(0x17, "ReadStatusOfDTCs (KWP2000)", KWP2000),
    ServiceInfo(0x18, "ReadDTCByStatus (KWP2000)", KWP2000),
    ServiceInfo(0x19, "ReadDTCInformation", UDS),
    ServiceInfo(0x1A, "ReadEcuIdentification", KWP2000, id_width=1),
    # 0x20 ends a KWP2000 diagnostic session (ISO 14230-3 6.2): the ECU releases any actuator
    # the tester drove and re-locks security. SSF 14230-3 drops it as redundant and has the
    # tester send 10 81 instead, which is the form the Ioniq's ECUs are known to accept.
    ServiceInfo(0x20, "StopDiagnosticSession (KWP2000)", KWP2000),
    ServiceInfo(0x21, "ReadDataByLocalIdentifier", KWP2000, id_width=1),
    # 0x22, 0x2E and 0x2F are byte-for-byte the same request on KWP2000, where ISO 14230-3
    # calls them ...ByCommonIdentifier (a 2-byte id); one entry carries the UDS name.
    ServiceInfo(0x22, "ReadDataByIdentifier", BOTH, id_width=2),
    ServiceInfo(0x23, "ReadMemoryByAddress", BOTH),
    # ISO 14230-3 lets 21/22/23 take a transmissionMode that makes the ECU repeat its reply;
    # 0x26 sets the repeat rates and 0x25 stops the repetition. SSF 14230-3 drops all three for
    # a periodicTransmissions session (10 82), which the Ioniq BMS, VCU, MCU and OBC refuse.
    ServiceInfo(0x25, "StopRepeatedDataTransmission (KWP2000)", KWP2000),
    ServiceInfo(0x26, "SetDataRates (KWP2000)", KWP2000),
    ServiceInfo(0x27, "SecurityAccess", BOTH),
    ServiceInfo(0x28, "CommunicationControl", UDS),
    # KWP2000 names this DynamicallyDefineLocalIdentifier, with a 1-byte target id rather than
    # UDS's sub-function + 2-byte DID, so the two share a SID but not a request layout.
    ServiceInfo(0x2C, "DynamicallyDefineDataIdentifier", BOTH),
    ServiceInfo(0x2E, "WriteDataByIdentifier", BOTH, id_width=2, actuates=True),
    ServiceInfo(
        0x2F,
        "InputOutputControlByIdentifier",
        BOTH,
        id_width=2,
        safe_discovery_sf=0x00,
        actuates=True,
    ),
    # No safe_discovery_sf: ISO 14230-3 leaves the option byte to the manufacturer,
    # and the SSF 14230-3 returnControlToECU (00) starts a one-shot command on Nissan.
    # canlib.actuation classifies a 0x30 request as a release only when declared.
    ServiceInfo(
        0x30,
        "InputOutputControlByLocalIdentifier",
        KWP2000,
        id_width=1,
        actuates=True,
    ),
    ServiceInfo(
        0x31,
        "RoutineControl (UDS) / StartRoutineByLocalIdentifier (KWP2000)",
        BOTH,
        id_width=2,
        safe_discovery_sf=0x03,
        actuates=True,
    ),
    # NOTE: safe_discovery_sf=0x03 (requestRoutineResults) is UDS-only. On a
    # KWP2000 ECU, 0x31 is StartRoutineByLocalIdentifier (actuates!) — its safe
    # "read results" is a *different service*, 0x33 below. The routines scanner
    # must pick 0x33 for KWP2000 ECUs and never blind-send 0x31 to them.
    ServiceInfo(0x32, "StopRoutineByLocalIdentifier", KWP2000, id_width=1, actuates=True),
    ServiceInfo(0x33, "RequestRoutineResultsByLocalIdentifier", KWP2000, id_width=1),
    ServiceInfo(0x34, "RequestDownload", BOTH, actuates=True),
    ServiceInfo(0x35, "RequestUpload", BOTH, actuates=True),
    ServiceInfo(0x36, "TransferData", BOTH, actuates=True),
    ServiceInfo(0x37, "RequestTransferExit", BOTH, actuates=True),
    # 0x38-0x3A are KWP2000's by-address routine trio: the address is 3 bytes, not an id, so
    # none carries an id_width. 0x38 starts code at a memory address; UDS reuses the byte for
    # file transfer, and both are unsafe to send.
    ServiceInfo(
        0x38,
        "RequestFileTransfer (UDS) / StartRoutineByAddress (KWP2000)",
        BOTH,
        actuates=True,
    ),
    ServiceInfo(0x39, "StopRoutineByAddress (KWP2000)", KWP2000, actuates=True),
    ServiceInfo(0x3A, "RequestRoutineResultsByAddress (KWP2000)", KWP2000),
    ServiceInfo(0x3B, "WriteDataByLocalIdentifier", KWP2000, id_width=1, actuates=True),
    ServiceInfo(0x3D, "WriteMemoryByAddress", BOTH, actuates=True),
    ServiceInfo(0x3E, "TesterPresent", BOTH),
    ServiceInfo(0x80, "EscapeCode (KWP2000)", KWP2000),
    ServiceInfo(0x85, "ControlDTCSetting", UDS),
)

_BY_SID: dict[int, ServiceInfo] = {s.sid: s for s in SERVICES}

# Negative-response marker (not a request service).
NEGATIVE_RESPONSE_SID = 0x7F
# Positive responses echo the request SID + this offset.
RESPONSE_SID_OFFSET = 0x40


def service_info(sid: int) -> ServiceInfo | None:
    """Return the :class:`ServiceInfo` for a request SID, or ``None`` if unknown."""
    return _BY_SID.get(sid)


def service_name(sid: int) -> str | None:
    """Human name for a request SID (e.g. ``0x30`` -> ``InputOutputControlByLocalIdentifier``).

    Returns ``None`` if the SID is not in the registry.
    """
    info = _BY_SID.get(sid)
    return info.name if info else None


def service_response_name(resp_sid: int) -> str | None:
    """Name a byte seen as the *first byte of a response*.

    Handles the ``+0x40`` positive-response echo (``0x6F`` -> the ``0x2F`` name,
    tagged as a response) and the ``0x7F`` negative-response marker. Returns
    ``None`` if it can't be mapped to a known service.
    """
    if resp_sid == NEGATIVE_RESPONSE_SID:
        return "NegativeResponse"
    if resp_sid in _BY_SID:  # a request SID echoed verbatim (rare)
        return _BY_SID[resp_sid].name
    request_sid = resp_sid - RESPONSE_SID_OFFSET
    info = _BY_SID.get(request_sid)
    return f"{info.name} (response)" if info else None
