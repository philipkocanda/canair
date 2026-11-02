"""``canair dtc ECU --clear``: erase an ECU's fault memory (``0x14``) and log what it held."""

from __future__ import annotations

import asyncio
import json

from canlib import ansi

from ...ecus import ecu_display
from ...transport.protocol import Terminal
from .codes import resolve_protocol
from .reading import _read_one
from .scan_log import _log_manual_clear


async def mode_dtc_clear(
    terminal: Terminal,
    tx_id: int,
    group: int = 0xFFFFFF,
    protocol: str = "auto",
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
    log: bool = False,
    label: str | None = None,
):
    """Clear stored DTCs via ClearDiagnosticInformation (0x14).

    UDS uses a 3-byte groupOfDTC (0xFFFFFF = all); KWP2000 uses 2 bytes
    (0xFFFF = all). Mutates ECU fault memory — confirmation is the caller's job.

    When ``log`` is set, the current codes are read first (so the log records
    what was cleared) and the clear is written to the DTC log, so a later scan
    reports these codes as wiped by the clear rather than as self-cleared.
    """
    proto = resolve_protocol(protocol, tx_id)
    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        # Read what's there first so the clear log is accurate (best-effort).
        pre_codes = None
        if log:
            pre = await _read_one(terminal, tx_id, 0xFF, protocol, verbose)
            if "dtcs" in pre:
                pre_codes = [d["dtc"] for d in pre["dtcs"]]

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

        base = {
            "ecu": ecu_display(tx_id),
            "protocol": proto,
            "command": cmd,
            "group": f"0x{g:0{4 if proto == 'kwp' else 6}X}",
        }
        if response["ok"]:
            if log:
                _log_manual_clear(tx_id, proto, base["group"], pre_codes, label)
            if as_json:
                print(json.dumps({**base, "cleared": True, "cleared_codes": pre_codes}))
            else:
                print(f"  ✓ DTCs cleared (group {base['group']}).")
                if pre_codes:
                    print(f"    {len(pre_codes)} code(s) cleared: {', '.join(pre_codes)}")
                elif pre_codes == []:
                    print("    (no codes were present)")
                if log:
                    print(f"  {ansi.DIM}Logged clear to the DTC log{ansi.RESET}")
                print()
        elif response.get("nrc") is not None:
            if as_json:
                print(
                    json.dumps(
                        {
                            **base,
                            "cleared": False,
                            "nrc": f"0x{response['nrc']:02X}",
                            "nrc_desc": response["nrc_desc"],
                        }
                    )
                )
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
