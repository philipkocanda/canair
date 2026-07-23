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
    # --- ISO 14230 KWP2000 diagnostic management / data ---
    ServiceInfo(0x10, "DiagnosticSessionControl", BOTH),
    ServiceInfo(0x11, "ECUReset", BOTH, actuates=True),
    ServiceInfo(0x14, "ClearDiagnosticInformation", BOTH),
    ServiceInfo(0x18, "ReadDTCByStatus (KWP2000)", KWP2000),
    ServiceInfo(0x19, "ReadDTCInformation", UDS),
    ServiceInfo(0x1A, "ReadEcuIdentification", KWP2000, id_width=1),
    ServiceInfo(0x21, "ReadDataByLocalIdentifier", KWP2000, id_width=1),
    ServiceInfo(0x22, "ReadDataByIdentifier", UDS, id_width=2),
    ServiceInfo(0x23, "ReadMemoryByAddress", BOTH),
    ServiceInfo(0x27, "SecurityAccess", BOTH),
    ServiceInfo(0x28, "CommunicationControl", UDS),
    ServiceInfo(0x2C, "DynamicallyDefineDataIdentifier", BOTH),
    ServiceInfo(0x2E, "WriteDataByIdentifier", UDS, id_width=2, actuates=True),
    ServiceInfo(
        0x2F,
        "InputOutputControlByIdentifier",
        UDS,
        id_width=2,
        safe_discovery_sf=0x00,
        actuates=True,
    ),
    ServiceInfo(
        0x30,
        "InputOutputControlByLocalIdentifier",
        KWP2000,
        id_width=1,
        safe_discovery_sf=0x00,
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
    ServiceInfo(0x38, "RequestFileTransfer", UDS, actuates=True),
    ServiceInfo(0x3B, "WriteDataByLocalIdentifier", KWP2000, id_width=1, actuates=True),
    ServiceInfo(0x3E, "TesterPresent", BOTH),
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
