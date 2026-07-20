"""Query standard UDS identity DIDs from an ECU."""

import asyncio
import json

from ..ecus import ecu_name
from ..terminal import WiCANTerminal

# Standard UDS identity DIDs (ISO 14229-1 / Hyundai-Kia common subset).
IDENTITY_DIDS: list[tuple[str, str, str]] = [
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


def _decode_identity_payload(payload_bytes: bytes, fmt: str) -> str:
    """Decode identity DID payload to a human-readable string."""
    stripped = payload_bytes.rstrip(b"\xaa\x00\xff")

    if not stripped:
        return "(empty)"
    if fmt == "date" and len(stripped) >= 3:
        hex_str = stripped.hex().upper()
        if len(hex_str) == 8:
            return f"{hex_str[0:4]}-{hex_str[4:6]}-{hex_str[6:8]}"
        elif len(hex_str) == 6:
            return f"20{hex_str[0:2]}-{hex_str[2:4]}-{hex_str[4:6]}"
        else:
            return stripped.hex().upper()

    if fmt == "ascii":
        printable = "".join(chr(b) if 32 <= b < 127 else "." for b in stripped)
        return printable if printable else stripped.hex().upper()

    return stripped.hex().upper()


async def mode_identity(
    terminal: WiCANTerminal, tx_id: int, session: bool, wake: bool, as_json: bool
):
    """Query standard UDS identity DIDs from an ECU."""
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    results = []
    try:
        print(f"\n  Identity query: {ecu_name(tx_id)} (0x{tx_id:03X})\n")
        label_width = max(len(label) for _, label, _ in IDENTITY_DIDS)

        for did_hex, label, fmt in IDENTITY_DIDS:
            response = await terminal.send_uds(f"22{did_hex}")
            if response["ok"]:
                payload = response["bytes"][3:]
                decoded = _decode_identity_payload(payload, fmt)
                raw_hex = payload.hex().upper()
                if as_json:
                    results.append(
                        {
                            "service": "22",
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

        if as_json:
            print(json.dumps(results, indent=2))

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
