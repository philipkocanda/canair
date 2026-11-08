"""Identity record tables for UDS and KWP2000 (data only).

Two families of ECU identity service exist on the vehicles this tool targets:

* **UDS** (ISO 14229) — ``22 F1xx`` ReadDataByIdentifier. Used by body/comfort
  ECUs (IGPM, BCM, clusters, ...).
* **KWP2000** (ISO 14230) — ``1A 8x/9x`` ReadEcuIdentification. Used by
  powertrain ECUs (BMS, VCU, MCU, OBC, gateways) which return
  ``NRC 0x11 serviceNotSupported`` to every UDS ``22 F1xx`` request.

A label is a short form of the standard's name: ISO 14229-1 Table C.1 for UDS, SSF 14230-3
Table 6.6.3 for KWP2000 ("VM" is the vehicle manufacturer). A record the standard leaves to
the manufacturer or supplier, with no known meaning, is labelled by its range. Where a make
uses a record its own way, a row with ``makes`` gives that make's label: on a profile of that
make it replaces the generic row of the same identifier, and a make row with no generic row
is read only there (:func:`records_for`). The comment after a row notes what the bundled cars
answer when it differs from the label.

``decode`` is one of ``ascii``/``date``/``hex``/``auto``/``session``/``modules``/``count``
(see ``decode.decode_identity_payload``).
"""

from typing import NamedTuple

from canair.uds.protocol import KWP, UDS


class IdentityRecord(NamedTuple):
    """One identity DID or KWP2000 record: what to ask, what to call it, how to show it."""

    id: str
    label: str
    decode: str = "ascii"
    makes: tuple[str, ...] = ()


#: The makes whose UDS identification options follow Hyundai/Kia's ES95486 Annex C.
HYUNDAI_KIA: tuple[str, ...] = ("hyundai", "kia")

#: The UDS DID that counts an ECU's software units, and the most units it may report
#: (ES95486 Annex C): the unit versions are F1B1 up to F1B0's count.
SW_UNIT_COUNT_DID = "F1B0"
MAX_SW_UNITS = 15

_R = IdentityRecord

UDS_IDENTITY_DIDS: list[IdentityRecord] = [
    _R("F190", "VIN"),  # masked through `sensitive:`
    _R("F187", "VM spare part number"),  # Hyundai/Kia: the part number
    _R("F188", "VM ECU software number"),
    _R("F189", "VM ECU software version"),  # IGPM, BCM: NRC 0x31
    _R("F18A", "System supplier ID"),
    _R("F18B", "ECU manufacturing date", "date"),
    _R("F18C", "ECU serial number"),
    _R("F18D", "Supported functional units", "hex"),
    _R("F18E", "VM kit assembly part number"),
    _R("F186", "Active diagnostic session", "session"),
    _R("F191", "VM ECU hardware number"),  # Hyundai/Kia: the vehicle HW version
    _R("F192", "Supplier ECU HW number"),
    _R("F193", "Supplier ECU HW version"),
    _R("F194", "Supplier ECU SW number"),
    _R("F195", "Supplier ECU SW version"),
    _R("F196", "Exhaust regulation / type approval no."),
    _R("F197", "System name / engine type"),
    _R("F199", "Programming date", "date"),
    _R("F19B", "Calibration date", "date"),
    _R("F19D", "ECU installation date", "date"),
    _R("F180", "Boot SW identification", "modules"),
    _R("F181", "Application SW identification", "modules"),
    _R("F182", "Application data identification", "modules"),
    _R("F183", "Boot SW fingerprint", "hex"),
    _R("F184", "Application SW fingerprint", "hex"),
    _R("F185", "Application data fingerprint", "hex"),
    _R("F100", "Manufacturer-specific F100"),
    # The B-CAN database version: a Hyundai/Kia supplier's ECU specification and test tool.
    _R("F100", "CAN DB version", makes=HYUNDAI_KIA),
    _R("F101", "Manufacturer-specific F101"),  # a Kona dealer tool: probably a CAN DB version
    _R("F110", "Manufacturer-specific F110"),
    _R("F17E", "Manufacturer-specific F17E"),
    _R("F1A0", "Manufacturer-specific F1A0"),
    _R("F1A0", "Software version", makes=HYUNDAI_KIA),  # ES95486 Annex C, F1A0
    _R("F1A1", "Supplier code", makes=HYUNDAI_KIA),  # ES95486 Annex C, F1A1
    # ES95486 has given F1A2 an application-version list only since 2023; the Ioniq's BCM
    # (2017) answers 620, so the label stays generic.
    _R("F1A2", "Manufacturer-specific F1A2"),
    _R("F1A4", "Manufacturer-specific F1A4"),
    _R(SW_UNIT_COUNT_DID, "Number of SW units", "count", HYUNDAI_KIA),  # then F1B1… per unit
]

KWP_IDENTITY_RECORDS: list[IdentityRecord] = [
    _R("80", "ECU identification data table", "auto"),
    _R("86", "Manufacturer-specific 86", "auto"),
    _R("87", "VM spare part number"),
    _R("88", "VM ECU software number"),
    _R("89", "VM ECU software version"),  # BMS, OBC, VCU, MCU: NRC 0x12
    _R("8A", "System supplier"),
    _R("8B", "ECU manufacturing date", "date"),
    _R("8C", "ECU serial number"),
    # The Ioniq's powertrain ECUs answer a module code that ends in a version (AEEOBC51).
    _R("8C", "Module code", makes=HYUNDAI_KIA),
    _R("8D", "Supplier-specific 8D"),  # Ioniq: a software id; the BMS's ROM ID
    _R("8E", "Supplier-specific 8E"),
    _R("90", "VIN"),  # Ioniq BMS: its ECU name
    _R("91", "VM ECU hardware number"),  # Ioniq: the part number; BMS: VER.2.3
    _R("92", "Supplier ECU HW number"),
    _R("93", "Supplier ECU HW version"),
    _R("94", "Supplier ECU SW number"),
    _R("95", "Supplier ECU SW version"),
    _R("96", "Exhaust regulation / type approval no."),
    _R("97", "System name / engine type"),
    _R("98", "Repair shop code / tester serial"),  # Ioniq AAF: a software id
    _R("99", "Programming date", "date"),
    _R("9A", "Calibration shop code / equipment serial"),
    _R("9B", "Calibration date", "date"),
    _R("9D", "ECU installation date", "date"),
]

# Backward-compatible alias (older imports expect ``IDENTITY_DIDS``).
IDENTITY_DIDS = UDS_IDENTITY_DIDS

# Per-protocol wire details: request SID prefix, positive-response payload
# offset (bytes to skip past SID + identifier echo), and the record table.
PROTOCOLS: dict[str, dict] = {
    UDS: {"prefix": "22", "payload_offset": 3, "records": UDS_IDENTITY_DIDS},
    KWP: {"prefix": "1A", "payload_offset": 2, "records": KWP_IDENTITY_RECORDS},
}


def records_for(protocol: str, make: str | None) -> list[IdentityRecord]:
    """The records ``canair identity`` reads for ``protocol`` on a profile of ``make``."""
    rows: list[IdentityRecord] = PROTOCOLS[protocol]["records"]
    own = {r.id for r in rows if make in r.makes}
    return [r for r in rows if (make in r.makes if r.makes else r.id not in own)]


def sw_unit_records(count: int) -> list[IdentityRecord]:
    """The software-unit version DIDs an ECU that reports ``count`` units answers."""
    return [
        _R(f"F1B{n:X}", f"SW unit {n} version", makes=HYUNDAI_KIA)
        for n in range(1, min(count, MAX_SW_UNITS) + 1)
    ]
