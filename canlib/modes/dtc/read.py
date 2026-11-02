"""``canair dtc ECU``: read one ECU's stored DTCs, report them, and log the scan."""

from __future__ import annotations

import asyncio
import json

from canlib import ansi

from ...dtc.refs import ecu_ref
from ...ecus import ecu_display
from ...transport.protocol import Terminal
from .codes import resolve_protocol
from .reading import _read_one
from .scan_log import _log_scan, _print_log_result


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
    if result.get("note"):
        print(f"  {ansi.YELLOW}Note:{ansi.RESET} {result['note']}")
    dtcs = result["dtcs"]
    supported = result.get("supported")
    if not dtcs:
        print("  No supported DTCs reported.\n" if supported else "  No DTCs stored.\n")
        return
    print(f"\n  {len(dtcs)} {'supported ' if supported else ''}DTC(s):\n")
    dtc_w = max(max(len(d["dtc"]) for d in dtcs), len("DTC"))
    for d in dtcs:
        flags = ", ".join(d["status_bits"]) or "(raw status)"
        print(f"  {d['dtc']:<{dtc_w}}  {d['status']}  {flags}")
        interp = d.get("interpretation") or {}
        meaning = interp.get("meaning")
        if meaning:
            print(f"  {'':<{dtc_w}}  → {meaning}")
    print()


async def mode_dtc_read(
    terminal: Terminal,
    tx_id: int,
    mask: int = 0xFF,
    protocol: str = "auto",
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
    log: bool = False,
    label: str | None = None,
    vehicle_states=None,
    supported: bool = False,
):
    """Read stored DTCs, auto-selecting UDS 0x19 or KWP2000 0x18 by protocol.

    ``supported`` lists every DTC a KWP2000 ECU can report instead (``18 03 FF 00``). That is
    a catalogue, not a fault list, so it is never written to the DTC log.
    """
    proto = resolve_protocol(protocol, tx_id)
    if supported and proto != "kwp":
        raise ValueError("--supported is only available for KWP2000 ECUs (id_protocol: KWP2000)")
    if supported:
        log = False

    tester_task = None
    if session:
        await terminal.set_header(tx_id)
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        if not as_json:
            fam = (
                "UDS 19 02 (reportDTCByStatusMask)"
                if proto == "uds"
                else "KWP2000 18 (readDTCByStatus)"
            )
            if supported:
                fam += ", supported-DTC list"
            print(f"\n  DTC read: {ecu_display(tx_id)}")
            print(f"  Protocol: {fam}\n")

        result = await _read_one(terminal, tx_id, mask, protocol, verbose, supported=supported)
        _report_read(as_json, result)
        if log and "dtcs" in result:
            path, previous, diff = _log_scan(ecu_ref(tx_id), [result], label, vehicle_states)
            if not as_json:
                _print_log_result(path, previous, diff)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
