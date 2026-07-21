"""Query ECU identity data across UDS and KWP2000 diagnostic protocols.

Two families of identity service exist on the vehicles this tool targets:

* **UDS** (ISO 14229) — ``22 F1xx`` ReadDataByIdentifier. Used by body/comfort
  ECUs (IGPM, BCM, clusters, ...).
* **KWP2000** (ISO 14230) — ``1A 8x/9x`` ReadEcuIdentification. Used by
  powertrain ECUs (BMS, VCU, MCU, LDC/OBC, gateways) which return
  ``NRC 0x11 serviceNotSupported`` to every UDS ``22 F1xx`` request.

``mode_identity`` picks the right family from an explicit ``--protocol`` flag,
then the profile's ``id_protocol`` registry hint, then a lightweight on-device
probe — so it works across a broad range of ECUs and manufacturers instead of
silently printing nothing on KWP2000 ECUs.
"""

import asyncio
import json

from ..ecus import ecu_display, ecu_id_protocol
from ..terminal import WiCANTerminal

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
_PROTOCOLS = {
    "uds": {"prefix": "22", "payload_offset": 3, "records": UDS_IDENTITY_DIDS},
    "kwp": {"prefix": "1A", "payload_offset": 2, "records": KWP_IDENTITY_RECORDS},
}


def _decode_identity_payload(payload_bytes: bytes, fmt: str) -> str:
    """Decode an identity payload to a human-readable string.

    ``fmt`` is a hint (``ascii``/``date``/``hex``/``auto``); ASCII and auto both
    fall back to hex when the bytes are not mostly printable text.
    """
    stripped = payload_bytes.rstrip(b"\xaa\x00\xff").lstrip(b"\x00")

    if not stripped:
        return "(empty)"

    if fmt == "date" and len(stripped) >= 3:
        hex_str = stripped.hex().upper()
        if len(hex_str) == 8:
            return f"{hex_str[0:4]}-{hex_str[4:6]}-{hex_str[6:8]}"
        if len(hex_str) == 6:
            return f"20{hex_str[0:2]}-{hex_str[2:4]}-{hex_str[4:6]}"
        return hex_str

    if fmt in ("ascii", "auto"):
        printable = sum(1 for b in stripped if 32 <= b < 127)
        if printable >= max(1, len(stripped)) * 0.6:
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in stripped)
            return text.strip() or stripped.hex().upper()

    return stripped.hex().upper()


def _resolve_protocol_hint(tx_id: int, requested: str) -> str | None:
    """Resolve the requested/registry protocol to ``"uds"``/``"kwp"``/``None``.

    ``requested`` is the user's ``--protocol`` (``auto``/``uds``/``kwp``).
    Returns ``None`` when it should be auto-probed on the device.
    """
    requested = (requested or "auto").lower()
    if requested in ("uds", "kwp"):
        return requested
    hint = (ecu_id_protocol(tx_id) or "").upper()
    if hint == "UDS":
        return "uds"
    if hint.startswith("KWP"):
        return "kwp"
    return None  # "none"/"unknown"/missing -> probe


def _service_supported(resp: dict) -> bool | None:
    """Interpret a probe response: True=supported, False=not, None=no signal.

    A positive response or any NRC other than serviceNotSupported (0x11) /
    serviceNotSupportedInActiveSession (0x7F) means the service exists. A bare
    ``NO DATA``/timeout carries no signal (ECU asleep or busy).
    """
    if resp.get("ok"):
        return True
    nrc = resp.get("nrc")
    if nrc is not None:
        return nrc not in (0x11, 0x7F)
    return None


async def _probe_protocol(terminal: WiCANTerminal) -> tuple[str | None, str]:
    """Probe the ECU to decide UDS vs KWP2000. Returns (protocol, reason)."""
    uds = await terminal.send_uds("22F190")
    uds_ok = _service_supported(uds)
    if uds_ok:
        return "uds", "UDS service 22 supported (probe 22F190)"

    kwp = await terminal.send_uds("1A90")
    kwp_ok = _service_supported(kwp)
    if kwp_ok:
        return "kwp", "KWP2000 service 1A supported (probe 1A90)"

    if uds_ok is False and kwp_ok is False:
        return None, "neither UDS 22 nor KWP2000 1A supported (both NRC 0x11)"
    return None, "no response to probe (22F190 / 1A90) — ECU may be asleep or unpowered"


async def mode_identity(
    terminal: WiCANTerminal,
    tx_id: int,
    session: bool,
    wake: bool,
    as_json: bool,
    protocol: str = "auto",
):
    """Query identity DIDs/records from an ECU across UDS and KWP2000."""
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        resolved = _resolve_protocol_hint(tx_id, protocol)
        probe_reason = None
        if resolved is None:
            resolved, probe_reason = await _probe_protocol(terminal)

        if not as_json:
            print(f"\n  Identity query: {ecu_display(tx_id)}")
            if resolved:
                fam = "UDS (22 F1xx)" if resolved == "uds" else "KWP2000 (1A 8x/9x)"
                detail = f" — {probe_reason}" if probe_reason else ""
                print(f"  Protocol: {fam}{detail}\n")
            else:
                print()

        if resolved is None:
            msg = probe_reason or "could not determine identity protocol"
            if as_json:
                print(json.dumps({"ecu": ecu_display(tx_id), "protocol": None, "results": []}))
            else:
                print(f"  No identity data: {msg}.")
                if "asleep" in msg:
                    print("  Try --session/--wake, or power the ECU (ACC/ignition on).")
            return

        spec = _PROTOCOLS[resolved]
        records = spec["records"]
        offset = spec["payload_offset"]
        prefix = spec["prefix"]
        label_width = max(len(label) for _, label, _ in records)

        results = []
        n_ok = n_nrc = n_nodata = 0

        for did_hex, label, fmt in records:
            response = await terminal.send_uds(f"{prefix}{did_hex}")
            if response["ok"]:
                n_ok += 1
                payload = response["bytes"][offset:]
                decoded = _decode_identity_payload(payload, fmt)
                raw_hex = payload.hex().upper()
                if as_json:
                    results.append(
                        {
                            "service": prefix,
                            "did": did_hex,
                            "label": label,
                            "decoded": decoded,
                            "raw": raw_hex,
                        }
                    )
                else:
                    print(f"  {did_hex}  {label:<{label_width}}  {decoded}")
                    if fmt != "ascii" or "." in decoded:
                        print(f"        {'':>{label_width}}  raw: {raw_hex}")
            elif response.get("nrc") is not None:
                n_nrc += 1
            else:
                n_nodata += 1

        if as_json:
            print(
                json.dumps(
                    {
                        "ecu": ecu_display(tx_id),
                        "protocol": resolved,
                        "summary": {"ok": n_ok, "nrc": n_nrc, "no_data": n_nodata},
                        "results": results,
                    },
                    indent=2,
                )
            )
        else:
            if n_ok == 0:
                if n_nodata and not n_nrc:
                    print("  No response — ECU may be asleep or unpowered.")
                    print("  Try --session/--wake, or power the ECU (ACC/ignition on).")
                else:
                    print("  No identity fields returned (all DIDs rejected/unsupported).")
            print(
                f"\n  {n_ok} field(s) returned, {n_nrc} rejected, "
                f"{n_nodata} no-response (of {len(records)} probed)."
            )

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
