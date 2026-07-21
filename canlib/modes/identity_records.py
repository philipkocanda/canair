"""Identity record tables for UDS and KWP2000 (data only).

Two families of ECU identity service exist on the vehicles this tool targets:

* **UDS** (ISO 14229) — ``22 F1xx`` ReadDataByIdentifier. Used by body/comfort
  ECUs (IGPM, BCM, clusters, ...).
* **KWP2000** (ISO 14230) — ``1A 8x/9x`` ReadEcuIdentification. Used by
  powertrain ECUs (BMS, VCU, MCU, LDC/OBC, gateways) which return
  ``NRC 0x11 serviceNotSupported`` to every UDS ``22 F1xx`` request.

Each entry is ``(identifier_hex, label, decode_hint)`` where the hint is one of
``ascii``/``date``/``hex``/``auto`` (see ``identity_decode.decode_identity_payload``).
"""

# UDS ReadDataByIdentifier (service 22) identity DIDs.
# (ISO 14229-1 F1xx range + the Hyundai/Kia -1 offset variants.)
UDS_IDENTITY_DIDS: list[tuple[str, str, str]] = [
    ("F190", "VIN", "ascii"),
    ("F188", "ECU Part Number (UDS)", "ascii"),
    ("F187", "ECU Part Number (HK)", "ascii"),
    ("F18C", "ECU Serial / Cal ID", "ascii"),
    ("F18B", "Manufacture Date", "date"),
    ("F18D", "ECU Manufacturing Date", "date"),
    ("F191", "HW Version Number", "ascii"),
    ("F100", "Boot SW ID", "ascii"),
    ("F101", "App SW ID", "ascii"),
    ("F110", "ECU Identification", "ascii"),
    ("F17E", "SW Install Date", "date"),
    ("F18A", "System Supplier ID", "ascii"),
    ("F192", "Supplier HW Number", "ascii"),
    ("F193", "Supplier HW Version", "ascii"),
    ("F194", "Supplier SW Number", "ascii"),
    ("F195", "Supplier SW Version", "ascii"),
    ("F196", "Exhaust Regulation / SW", "ascii"),
    ("F197", "System / Engine Name", "ascii"),
    ("F1A0", "Diagnostic Address", "hex"),
    ("F1A2", "HW Version", "ascii"),
    ("F1A4", "HW Part 2", "ascii"),
]

# KWP2000 ReadEcuIdentification (service 1A) records.
# Labels follow the Hyundai/Kia semantics observed on Ioniq powertrain ECUs;
# the ISO 14230 standard record names are noted where they differ.
KWP_IDENTITY_RECORDS: list[tuple[str, str, str]] = [
    ("80", "General ECU Identification", "auto"),
    ("86", "DCS ECU Identification", "auto"),
    ("87", "Spare Part Number", "ascii"),
    ("88", "ECU Software Number", "ascii"),
    ("8A", "System Supplier ID", "ascii"),
    ("8B", "Manufacture / Version Date", "date"),
    ("8C", "ECU Software / ID", "ascii"),
    ("8D", "Software Identifier", "ascii"),
    ("8E", "Calibration Identifier", "ascii"),
    ("90", "ECU Name / VIN", "ascii"),
    ("91", "Firmware Version / Part No.", "ascii"),
    ("92", "Hardware Version", "ascii"),
    ("94", "Supplier SW Number", "ascii"),
    ("95", "Supplier SW Version", "ascii"),
    ("96", "Supplier SW / Regulation", "ascii"),
    ("97", "System / Engine Name", "ascii"),
    ("98", "Firmware Identifier", "ascii"),
    ("99", "Programming Date", "date"),
    ("9A", "Repair Shop / Tester", "ascii"),
]

# Backward-compatible alias (older imports expect ``IDENTITY_DIDS``).
IDENTITY_DIDS = UDS_IDENTITY_DIDS

# Per-protocol wire details: request SID prefix, positive-response payload
# offset (bytes to skip past SID + identifier echo), and the record table.
PROTOCOLS: dict[str, dict] = {
    "uds": {"prefix": "22", "payload_offset": 3, "records": UDS_IDENTITY_DIDS},
    "kwp": {"prefix": "1A", "payload_offset": 2, "records": KWP_IDENTITY_RECORDS},
}
