"""Query all parameters for an ECU."""

import asyncio
import json

from ..expression import evaluate_expression
from ..formatting import print_decoded_params
from ..pids import build_ecu_index
from ..terminal import WiCANTerminal
from ..wican_bytes import uds_hex_to_wican_bytes


async def mode_ecu(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecu_name: str,
    pid_filter: str | None,
    verbose: bool,
    as_json: bool,
    session: bool = False,
    wake: bool = False,
):
    """Query all parameters for an ECU, optionally filtered by PID."""
    ecu_index = build_ecu_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in ecu_index:
        print(f"  Unknown ECU: {ecu_name}")
        print(f"  Available: {', '.join(sorted(ecu_index.keys()))}")
        return

    ecu_info = ecu_index[ecu_key]
    tx_id = ecu_info["tx_id"]

    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    print(f"\n  {ecu_key} -- TX 0x{tx_id:03X}")

    all_json = []

    try:
        for pid_code, pid_info in sorted(ecu_info["pids"].items()):
            if pid_filter and pid_code.upper() != pid_filter.upper():
                continue

            parameters = pid_info["parameters"]
            if not parameters:
                continue

            print(f"\n  PID {pid_code}:")

            response = await terminal.send_uds(pid_code)

            if not response["ok"]:
                error = response.get("error") or response.get("nrc_desc", "unknown error")
                if response.get("nrc") is not None:
                    error = f"NRC 0x{response['nrc']:02X}: {response['nrc_desc']}"
                print(f"    Error: {error}")
                continue

            wican_bytes = uds_hex_to_wican_bytes(response["hex"])

            if verbose:
                print(f"    Response: {response['hex']}")
                print(f"    WiCAN bytes ({len(wican_bytes)}): {wican_bytes.hex().upper()}")

            results = []
            for pname, pdef in parameters.items():
                expr = pdef.get("expression", "")
                unit = pdef.get("unit", "")
                verified = pdef.get("verified", False)
                if not expr:
                    continue
                try:
                    value = evaluate_expression(expr, wican_bytes)
                    value = round(value * 100) / 100
                    results.append((pname, value, unit, expr, None, verified))
                except Exception as e:
                    results.append((pname, None, unit, expr, str(e), verified))

            if as_json:
                for name, value, unit, _expr, error, _verified in results:
                    entry = {
                        "ecu": ecu_key,
                        "pid": pid_code,
                        "name": name,
                        "value": value,
                        "unit": unit,
                    }
                    if error:
                        entry["error"] = error
                    all_json.append(entry)
            else:
                print_decoded_params(results, verbose=verbose)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

    if as_json:
        print(json.dumps(all_json, indent=2))

    print()
