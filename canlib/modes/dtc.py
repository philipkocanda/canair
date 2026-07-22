"""DTC mode — read and clear Diagnostic Trouble Codes across UDS and KWP2000.

The Ioniq mixes two diagnostic protocols, so DTC access is protocol-aware
(auto-selected from the profile's ``id_protocol`` registry, like ``identity``):

* **UDS** ECUs (BCM, IGPM, ...) use ReadDTCInformation ``0x19`` subfunction
  ``0x02`` (reportDTCByStatusMask) and ClearDiagnosticInformation ``0x14`` with a
  3-byte groupOfDTC. DTC records are 4 bytes (3-byte DTC + 1-byte status).
* **KWP2000** ECUs (BMS, VCU, MCU, LDC, ...) use readDiagnosticTroubleCodesBy
  Status ``0x18`` and clearDiagnosticInformation ``0x14`` with a 2-byte group.
  DTC records are 3 bytes (2-byte DTC + 1-byte status).

Clearing mutates ECU fault memory; the caller confirms intent before invoking.
"""

from __future__ import annotations

import asyncio
import json

from ..ecus import ecu_display
from ..terminal import WiCANTerminal

# SAE J2012 DTC category letters, selected by the top two bits of the first byte.
_DTC_LETTERS = ("P", "C", "B", "U")

# statusOfDTC bit meanings (ISO 14229-1 / UDS), bit 0 = LSB.
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

# UDS status mask to fall back to when an ECU rejects 0xFF with requestOutOfRange
# (NRC 0x31). Some Hyundai ECUs (e.g. IGPM) only accept a mask within their
# availability bits; confirmedDTC (0x08) is the most widely supported.
_MASK_FALLBACK = 0x08

# KWP2000 readDiagnosticTroubleCodesByStatus (0x18) operands vary between Hyundai
# ECUs; try the common forms until one returns a positive 0x58 response. Reads
# are non-mutative, so probing is safe.
_KWP_READ_REQUESTS = ("1800FF00", "1802FF00", "1800FFFF")


def format_dtc(b0: int, b1: int, b2: int) -> str:
    """Format a 3-byte UDS DTC as ``Lxxxx-yy`` (SAE J2012 + failure type)."""
    letter = _DTC_LETTERS[(b0 >> 6) & 0x03]
    d1 = (b0 >> 4) & 0x03
    d2 = b0 & 0x0F
    return f"{letter}{d1}{d2:X}{b1:02X}-{b2:02X}"


def format_kwp_dtc(b0: int, b1: int) -> str:
    """Format a 2-byte KWP2000 DTC as ``Lxxxx`` (no failure-type byte)."""
    letter = _DTC_LETTERS[(b0 >> 6) & 0x03]
    d1 = (b0 >> 4) & 0x03
    d2 = b0 & 0x0F
    return f"{letter}{d1}{d2:X}{b1:02X}"


def decode_status(status: int) -> list[str]:
    """Return the names of the set UDS statusOfDTC bits, LSB first."""
    return [name for bit, name in enumerate(_STATUS_BITS) if status & (1 << bit)]


def decode_dtc_records(data: bytes) -> list[dict]:
    """Decode UDS 4-byte DTC records (3-byte DTC + 1-byte status)."""
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


def decode_kwp_dtc_records(data: bytes) -> list[dict]:
    """Decode KWP2000 3-byte DTC records (2-byte DTC + 1-byte status).

    KWP2000 statusOfDTC bit semantics are not the UDS layout, so the raw status
    byte is reported without named bits.
    """
    records = []
    for i in range(0, len(data) - 2, 3):
        b0, b1, status = data[i], data[i + 1], data[i + 2]
        records.append(
            {
                "dtc": format_kwp_dtc(b0, b1),
                "raw": f"{b0:02X}{b1:02X}",
                "status": status,
                "status_bits": [],
            }
        )
    return records


def resolve_protocol(protocol: str, tx_id: int) -> str:
    """Resolve ``auto`` to ``uds``/``kwp`` from the ECU's id_protocol registry."""
    if protocol in ("uds", "kwp"):
        return protocol
    from ..ecus import ecu_id_protocol

    hint = str(ecu_id_protocol(tx_id) or "").upper()
    return "kwp" if hint.startswith("KWP") else "uds"


def _report_read(as_json: bool, result: dict) -> None:
    """Print a read result (positive, NRC, or error) as text or JSON."""
    if as_json:
        print(json.dumps(result, indent=2))
        return

    if "nrc" in result:
        print(f"  ✗ NRC {result['nrc']}: {result['nrc_desc']}\n")
        return
    if "error" in result:
        print(f"  ✗ Error: {result['error']}")
        if "NO DATA" in result["error"] or "No response" in result["error"]:
            print("  ECU may be asleep or unpowered — try --session/--wake.")
        print()
        return

    if result.get("status_availability_mask") is not None:
        print(f"  Status availability mask: {result['status_availability_mask']}")
    dtcs = result["dtcs"]
    if not dtcs:
        print("  No DTCs stored.\n")
        return
    print(f"\n  {len(dtcs)} DTC(s):\n")
    dtc_w = max(max(len(d["dtc"]) for d in dtcs), len("DTC"))
    for d in dtcs:
        flags = ", ".join(d["status_bits"]) or "(raw status)"
        print(f"  {d['dtc']:<{dtc_w}}  {d['status']}  {flags}")
        interp = d.get("interpretation") or {}
        meaning = interp.get("description") or interp.get("meaning")
        if meaning:
            print(f"  {'':<{dtc_w}}  → {meaning}")
    print()


async def mode_dtc_read(
    terminal: WiCANTerminal,
    tx_id: int,
    mask: int = 0xFF,
    protocol: str = "auto",
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
):
    """Read stored DTCs, auto-selecting UDS 0x19 or KWP2000 0x18 by protocol."""
    proto = resolve_protocol(protocol, tx_id)

    tester_task = None
    if session:
        await terminal.set_header(tx_id)
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        if not as_json:
            fam = "UDS 19 02 (reportDTCByStatusMask)" if proto == "uds" else \
                "KWP2000 18 (readDTCByStatus)"
            print(f"\n  DTC read: {ecu_display(tx_id)}")
            print(f"  Protocol: {fam}\n")

        result = await _read_one(terminal, tx_id, mask, protocol, verbose)
        _report_read(as_json, result)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass


async def _read_one(
    terminal: WiCANTerminal,
    tx_id: int,
    mask: int,
    protocol: str,
    verbose: bool,
    timeout: float = 3.0,
) -> dict:
    """Set the header and read DTCs from one ECU (no session handling)."""
    proto = resolve_protocol(protocol, tx_id)
    await terminal.set_header(tx_id)
    if proto == "kwp":
        return await _read_kwp(terminal, tx_id, timeout=timeout)
    return await _read_uds(terminal, tx_id, mask, verbose, timeout=timeout)


async def _read_uds(
    terminal: WiCANTerminal, tx_id: int, mask: int, verbose: bool, timeout: float = 3.0
) -> dict:
    ecu = ecu_display(tx_id)
    cmd = f"1902{mask & 0xFF:02X}"
    response = await terminal.send_uds(cmd, timeout=timeout, expected_sid=0x19)

    # Mask fallback: some ECUs reject 0xFF with requestOutOfRange (0x31) and only
    # accept a mask within their availability bits. Retry once with confirmedDTC.
    if (
        not response["ok"]
        and response.get("nrc") == 0x31
        and (mask & 0xFF) != _MASK_FALLBACK
    ):
        if verbose:
            print(f"  mask 0x{mask & 0xFF:02X} rejected (0x31); retrying with "
                  f"0x{_MASK_FALLBACK:02X}", flush=True)
        cmd = f"1902{_MASK_FALLBACK:02X}"
        response = await terminal.send_uds(cmd, timeout=timeout, expected_sid=0x19)

    base = {"ecu": ecu, "protocol": "uds", "command": cmd}
    if response["ok"]:
        data = response["bytes"]
        avail = data[2] if len(data) >= 3 else None
        records = decode_dtc_records(bytes(data[3:]))
        return {
            **base,
            "status_availability_mask": f"0x{avail:02X}" if avail is not None else None,
            "count": len(records),
            "dtcs": _jsonable(records),
        }
    if response.get("nrc") is not None:
        return {**base, "nrc": f"0x{response['nrc']:02X}", "nrc_desc": response["nrc_desc"]}
    return {**base, "error": response.get("error", "unknown")}


async def _read_kwp(terminal: WiCANTerminal, tx_id: int, timeout: float = 3.0) -> dict:
    ecu = ecu_display(tx_id)
    response = None
    cmd = _KWP_READ_REQUESTS[0]
    for cmd in _KWP_READ_REQUESTS:
        response = await terminal.send_uds(cmd, timeout=timeout, expected_sid=0x18)
        if response["ok"]:
            break
        # Only probe alternate request forms if the ECU actually answered (NRC);
        # no response means the ECU isn't present — stop probing.
        if response.get("nrc") is None:
            break

    base = {"ecu": ecu, "protocol": "kwp", "command": cmd}
    if response["ok"]:
        data = response["bytes"]
        # 58 <count> [dtc_hi dtc_lo status]* — count is advisory; decode by length.
        records = decode_kwp_dtc_records(bytes(data[2:]))
        return {**base, "count": len(records), "dtcs": _jsonable(records)}
    if response.get("nrc") is not None:
        return {**base, "nrc": f"0x{response['nrc']:02X}", "nrc_desc": response["nrc_desc"]}
    return {**base, "error": response.get("error", "unknown")}



def _jsonable(records: list[dict]) -> list[dict]:
    """Render internal records (int status) into the JSON/print-friendly shape,
    annotated with a structural interpretation of each code."""
    from ..dtc_describe import describe_dtc

    return [
        {
            "dtc": r["dtc"],
            "raw": r["raw"],
            "status": f"0x{r['status']:02X}",
            "status_bits": r["status_bits"],
            "interpretation": describe_dtc(r["dtc"]),
        }
        for r in records
    ]


def _scan_status(res: dict) -> str:
    """One-line result summary for a single ECU in an all-ECU scan."""
    if "dtcs" in res:
        n = res["count"]
        return f"{n} DTC(s)" if n else "clean"
    if "nrc" in res:
        from ..elm327 import nrc_abbrev

        return f"NRC {res['nrc']} {nrc_abbrev(int(res['nrc'], 16))}"
    err = res.get("error", "error")
    return "no response" if ("NO DATA" in err or "No response" in err) else err


async def mode_dtc_scan_all(
    terminal: WiCANTerminal,
    mask: int = 0xFF,
    protocol: str = "auto",
    as_json: bool = False,
    verbose: bool = False,
    timeout: float = 2.0,
):
    """Sweep every ECU in the profile registry and read its stored DTCs.

    Each ECU's service is auto-selected by its ``id_protocol`` (UDS 0x19 vs
    KWP2000 0x18). Reads run in the default session (no wake) — the same way the
    single-ECU reads succeed — and are strictly sequential (one connection).
    """
    from ..ecus import load_ecus

    registry = load_ecus()
    tx_ids = sorted(registry)

    if not as_json:
        print(f"\n  Scanning {len(tx_ids)} ECUs for DTCs "
              f"(protocol={protocol}, mask=0x{mask & 0xFF:02X})...\n")
        name_w = max((len(ecu_display(t)) for t in tx_ids), default=12)

    results = []
    for tx_id in tx_ids:
        res = await _read_one(terminal, tx_id, mask, protocol, verbose, timeout=timeout)
        results.append(res)
        if not as_json:
            status = _scan_status(res)
            print(f"  {res['ecu']:<{name_w}}  {res['protocol']:<4}  {status}")

    faulty = [r for r in results if r.get("count")]
    total_codes = sum(r["count"] for r in faulty)

    if as_json:
        print(json.dumps(
            {
                "scanned": len(results),
                "with_dtcs": len(faulty),
                "total_codes": total_codes,
                "results": results,
            },
            indent=2,
        ))
        return

    print()
    if not faulty:
        print(f"  ✓ No DTCs on any of the {len(results)} ECUs scanned.\n")
        return

    print(f"  ⚠ {len(faulty)} ECU(s) with DTCs — {total_codes} code(s) total:\n")
    for r in faulty:
        print(f"  {r['ecu']} ({r['protocol']}):")
        for d in r["dtcs"]:
            flags = ", ".join(d["status_bits"]) or "raw status"
            interp = d.get("interpretation") or {}
            meaning = interp.get("description") or interp.get("meaning") or ""
            print(f"      {d['dtc']}  {d['status']}  {flags}")
            if meaning:
                print(f"        → {meaning}")
    print()


async def mode_dtc_clear(
    terminal: WiCANTerminal,
    tx_id: int,
    group: int = 0xFFFFFF,
    protocol: str = "auto",
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
):
    """Clear stored DTCs via ClearDiagnosticInformation (0x14).

    UDS uses a 3-byte groupOfDTC (0xFFFFFF = all); KWP2000 uses 2 bytes
    (0xFFFF = all). Mutates ECU fault memory — confirmation is the caller's job.
    """
    proto = resolve_protocol(protocol, tx_id)
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        if proto == "kwp":
            g = group & 0xFFFF
            cmd = f"14{g:04X}"
        else:
            g = group & 0xFFFFFF
            cmd = f"14{g:06X}"

        if not as_json:
            print(f"\n  DTC clear: {ecu_display(tx_id)}")
            print(f"  Command: {cmd} (ClearDiagnosticInformation, {proto})\n")

        response = await terminal.send_uds(cmd, timeout=5.0, expected_sid=0x14)

        base = {"ecu": ecu_display(tx_id), "protocol": proto, "command": cmd,
                "group": f"0x{g:0{4 if proto == 'kwp' else 6}X}"}
        if response["ok"]:
            if as_json:
                print(json.dumps({**base, "cleared": True}))
            else:
                print(f"  ✓ DTCs cleared (group {base['group']}).\n")
        elif response.get("nrc") is not None:
            if as_json:
                print(json.dumps({**base, "cleared": False,
                                  "nrc": f"0x{response['nrc']:02X}",
                                  "nrc_desc": response["nrc_desc"]}))
            else:
                print(f"  ✗ NRC 0x{response['nrc']:02X}: {response['nrc_desc']}\n")
        else:
            error = response.get("error", "unknown")
            if as_json:
                print(json.dumps({**base, "cleared": False, "error": error}))
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
