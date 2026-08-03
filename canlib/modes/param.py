"""Query specific named parameters."""

import asyncio
import json

from ..decoding import decode_param_rows
from ..formatting import print_decoded_params
from ..pids import build_param_index
from ..transport.protocol import Terminal


def _param_def(p: dict) -> dict:
    """Reconstruct a parameter definition dict from a param-index entry.

    Carries the typed-decode fields (``type``/``values``/``bits``/``fields``) and
    ``display`` through so :func:`decode_param_rows` renders enum/bitmask labels
    and display expressions, not just the bare float.
    """
    return {
        "expression": p.get("expression", ""),
        "unit": p.get("unit", ""),
        "verified": p.get("verified", False),
        "display": p.get("display", ""),
        "type": p.get("type", ""),
        "values": p.get("values", {}),
        "bits": p.get("bits", {}),
        "fields": p.get("fields", []),
    }


async def mode_param(
    terminal: Terminal,
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
    opened: set[int] = set()  # ECUs we've already entered a session on

    try:
        for (tx_id, pid), params in groups.items():
            await terminal.set_header(tx_id)

            if session and tx_id not in opened:
                # One session (+ TesterPresent loop) per ECU, not per (ECU, PID) —
                # several params on the same ECU must not each open a session.
                _, tester_task = await terminal.enter_extended_session(wake=wake)
                tester_tasks.append(tester_task)
                opened.add(tx_id)

            response = await terminal.send_uds(pid, retries=1)

            if not response["ok"]:
                error = response.get("error") or response.get("nrc_desc", "unknown error")
                if response.get("nrc") is not None:
                    error = f"NRC 0x{response['nrc']:02X}: {response['nrc_desc']}"
                for p in params:
                    all_results.append(
                        (p["name"], None, p["unit"], p["expression"], error, p["verified"])
                    )
                continue

            # Decode the requested params (typed-aware — enum/bitmask labels,
            # display expressions) via the shared row decoder.
            param_defs = {p["name"]: _param_def(p) for p in params}
            all_results.extend(decode_param_rows(response["hex"], param_defs))
    finally:
        for task in tester_tasks:
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if as_json:
        json_out = []
        for row in all_results:
            name, value, unit, _expr, error = row[:5]
            entry = {"name": name, "value": value, "unit": unit}
            if error:
                entry["error"] = error
            json_out.append(entry)
        print(json.dumps(json_out, indent=2))
    else:
        print()
        print_decoded_params(all_results, verbose=verbose)
        print()
