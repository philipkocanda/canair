"""DTC mode — read and clear Diagnostic Trouble Codes over UDS.

Two UDS services cover the whole flow:

* ``0x19`` ReadDTCInformation — ``mode_dtc_read`` sends subfunction ``0x02``
  (reportDTCByStatusMask) with a status mask (default ``0xFF`` = all) and
  decodes the returned records.
* ``0x14`` ClearDiagnosticInformation — ``mode_dtc_clear`` clears a group of
  DTCs (default ``0xFFFFFF`` = all groups). This mutates ECU fault memory, so
  the caller is responsible for confirming intent before invoking it.

DTC records are 4 bytes each: a 3-byte DTC (ISO 14229 / SAE J2012) plus a
1-byte statusOfDTC bitfield.
"""

from __future__ import annotations

import asyncio
import json

from ..ecus import ecu_display
from ..terminal import WiCANTerminal

# SAE J2012 DTC category letters, selected by the top two bits of byte 0.
_DTC_LETTERS = ("P", "C", "B", "U")

# statusOfDTC bit meanings (ISO 14229-1), bit 0 = LSB.
_STATUS_BITS = (
    "testFailed",
    "testFailedThisOperationCycle",
    "pendingDTC",
    "confirmedDTC",
    "testNotCompletedSinceLastClear",
    "testFailedSinceLastClear",
    "testNotCompletedThisOperationCycle",
    "warningIndicatorRequested",
)


def format_dtc(b0: int, b1: int, b2: int) -> str:
    """Format a 3-byte UDS DTC as ``Lxxxx-yy`` (SAE J2012 + failure type).

    * bits 7-6 of ``b0`` select the category letter (P/C/B/U)
    * bits 5-4 of ``b0`` are the first digit, low nibble the second
    * ``b1`` is the third/fourth hex digits
    * ``b2`` is the failure-type byte, shown after a dash
    """
    letter = _DTC_LETTERS[(b0 >> 6) & 0x03]
    d1 = (b0 >> 4) & 0x03
    d2 = b0 & 0x0F
    return f"{letter}{d1}{d2:X}{b1:02X}-{b2:02X}"


def decode_status(status: int) -> list[str]:
    """Return the names of the set statusOfDTC bits, LSB first."""
    return [name for bit, name in enumerate(_STATUS_BITS) if status & (1 << bit)]


def decode_dtc_records(data: bytes) -> list[dict]:
    """Decode a run of 4-byte DTC records (3-byte DTC + 1-byte status).

    Trailing bytes that don't form a complete record are ignored.
    """
    records = []
    for i in range(0, len(data) - 3, 4):
        b0, b1, b2, status = data[i], data[i + 1], data[i + 2], data[i + 3]
        records.append(
            {
                "dtc": format_dtc(b0, b1, b2),
                "raw": f"{b0:02X}{b1:02X}{b2:02X}",
                "status": status,
                "status_bits": decode_status(status),
            }
        )
    return records


async def mode_dtc_read(
    terminal: WiCANTerminal,
    tx_id: int,
    mask: int = 0xFF,
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
):
    """Read stored DTCs via ReadDTCInformation (0x19 0x02 reportDTCByStatusMask)."""
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        cmd = f"1902{mask & 0xFF:02X}"
        if not as_json:
            print(f"\n  DTC read: {ecu_display(tx_id)}")
            print(f"  Command: 19 02 {mask & 0xFF:02X} (reportDTCByStatusMask)\n")

        response = await terminal.send_uds(cmd, timeout=3.0, expected_sid=0x19)

        if response["ok"]:
            data = response["bytes"]
            avail_mask = data[2] if len(data) >= 3 else None
            records = decode_dtc_records(bytes(data[3:]))

            if as_json:
                print(
                    json.dumps(
                        {
                            "ecu": ecu_display(tx_id),
                            "command": cmd,
                            "status_availability_mask": (
                                f"0x{avail_mask:02X}" if avail_mask is not None else None
                            ),
                            "count": len(records),
                            "dtcs": [
                                {
                                    "dtc": r["dtc"],
                                    "raw": r["raw"],
                                    "status": f"0x{r['status']:02X}",
                                    "status_bits": r["status_bits"],
                                }
                                for r in records
                            ],
                        },
                        indent=2,
                    )
                )
            else:
                if avail_mask is not None:
                    print(f"  Status availability mask: 0x{avail_mask:02X}")
                if not records:
                    print("  No DTCs stored.\n")
                else:
                    print(f"\n  {len(records)} DTC(s):\n")
                    dtc_w = max(len(r["dtc"]) for r in records)
                    dtc_w = max(dtc_w, len("DTC"))
                    print(f"  {'DTC':<{dtc_w}}  Status  Flags")
                    print(f"  {'─' * (dtc_w + 8 + 40)}")
                    for r in records:
                        flags = ", ".join(r["status_bits"]) or "(none)"
                        print(f"  {r['dtc']:<{dtc_w}}  0x{r['status']:02X}    {flags}")
                    print()
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            if as_json:
                print(
                    json.dumps(
                        {"ecu": ecu_display(tx_id), "command": cmd, "nrc": f"0x{nrc:02X}",
                         "nrc_desc": desc}
                    )
                )
            else:
                print(f"  ✗ NRC 0x{nrc:02X}: {desc}\n")
        else:
            error = response.get("error", "unknown")
            if as_json:
                print(json.dumps({"ecu": ecu_display(tx_id), "command": cmd, "error": error}))
            else:
                print(f"  ✗ Error: {error}")
                if "NO DATA" in error or "No response" in error:
                    print("  ECU may be asleep or unpowered — try --session/--wake.")
                print()

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass


async def mode_dtc_clear(
    terminal: WiCANTerminal,
    tx_id: int,
    group: int = 0xFFFFFF,
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
):
    """Clear stored DTCs via ClearDiagnosticInformation (0x14).

    ``group`` is the 3-byte groupOfDTC (0xFFFFFF = all). This mutates ECU
    fault memory; confirmation is the caller's responsibility.
    """
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        cmd = f"14{group & 0xFFFFFF:06X}"
        if not as_json:
            print(f"\n  DTC clear: {ecu_display(tx_id)}")
            print(f"  Command: 14 {group & 0xFFFFFF:06X} (ClearDiagnosticInformation)\n")

        response = await terminal.send_uds(cmd, timeout=5.0, expected_sid=0x14)

        if response["ok"]:
            if as_json:
                print(
                    json.dumps(
                        {
                            "ecu": ecu_display(tx_id),
                            "command": cmd,
                            "group": f"0x{group & 0xFFFFFF:06X}",
                            "cleared": True,
                        }
                    )
                )
            else:
                print(f"  ✓ DTCs cleared (group 0x{group & 0xFFFFFF:06X}).\n")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            if as_json:
                print(
                    json.dumps(
                        {"ecu": ecu_display(tx_id), "command": cmd, "cleared": False,
                         "nrc": f"0x{nrc:02X}", "nrc_desc": desc}
                    )
                )
            else:
                print(f"  ✗ NRC 0x{nrc:02X}: {desc}\n")
        else:
            error = response.get("error", "unknown")
            if as_json:
                print(
                    json.dumps(
                        {"ecu": ecu_display(tx_id), "command": cmd, "cleared": False,
                         "error": error}
                    )
                )
            else:
                print(f"  ✗ Error: {error}")
                if "NO DATA" in error or "No response" in error:
                    print("  ECU may be asleep or unpowered — try --session/--wake.")
                print()

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
