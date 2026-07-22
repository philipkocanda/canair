"""Query specific named parameters."""

import asyncio
import json

from ..expression import evaluate_expression
from ..formatting import print_decoded_params
from ..pids import build_param_index
from ..terminal import WiCANTerminal
from ..wican_bytes import uds_hex_to_wican_bytes


async def mode_param(
    terminal: WiCANTerminal,
    pids_data: dict,
    param_names: list[str],
    verbose: bool,
    as_json: bool,
    session: bool = False,
    wake: bool = False,
):
    """Query specific named parameters."""
    param_index = build_param_index(pids_data)

    groups: dict[tuple[int, str], list[dict]] = {}
    for name in param_names:
        key = name.upper()
        if key not in param_index:
            print(f"  Unknown parameter: {name}")
            matches = [k for k in param_index if key in k]
            if matches:
                print(f"  Did you mean: {', '.join(matches[:5])}")
            continue
        info = param_index[key]
        group_key = (info["tx_id"], info["pid"])
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append({**info, "name": key})

    if not groups:
        return

    all_results = []
    tester_tasks = []

    try:
        for (tx_id, pid), params in groups.items():
            await terminal.set_header(tx_id)

            if session:
                _, tester_task = await terminal.enter_extended_session(wake=wake)
                tester_tasks.append(tester_task)

            response = await terminal.send_uds(pid)

            if not response["ok"]:
                error = response.get("error") or response.get("nrc_desc", "unknown error")
                if response.get("nrc") is not None:
                    error = f"NRC 0x{response['nrc']:02X}: {response['nrc_desc']}"
                for p in params:
                    all_results.append(
                        (p["name"], None, p["unit"], p["expression"], error, p["verified"])
                    )
                continue

            wican_bytes = uds_hex_to_wican_bytes(response["hex"])

            for p in params:
                try:
                    value = evaluate_expression(p["expression"], wican_bytes)
                    value = round(value * 100) / 100
                    all_results.append(
                        (p["name"], value, p["unit"], p["expression"], None, p["verified"])
                    )
                except Exception as e:
                    all_results.append(
                        (p["name"], None, p["unit"], p["expression"], str(e), p["verified"])
                    )
    finally:
        for task in tester_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if as_json:
        json_out = []
        for name, value, unit, _expr, error, _verified in all_results:
            entry = {"name": name, "value": value, "unit": unit}
            if error:
                entry["error"] = error
            json_out.append(entry)
        print(json.dumps(json_out, indent=2))
    else:
        print()
        print_decoded_params(all_results, verbose=verbose)
        print()
