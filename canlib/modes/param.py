"""Query specific named parameters."""

import json

from canlib.ecus.pids import build_param_index
from canlib.expression.decoding import decode_param_rows
from canlib.privacy.sensitive import mask_rows
from canlib.uds.echo import request_echo

from ..formatting import print_decoded_params
from ..session_manager import SessionManager
from ..transport.protocol import Terminal
from .multi_exec import timed_exchange


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
        "sensitive": p.get("sensitive", False),
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
    # One session per ECU, each kept alive on its own header. The terminal's own
    # TesterPresent loop sends 3E00 to whichever ECU the adapter is addressed at
    # when it fires, so with two ECUs the first one's session lapsed while the
    # second was read.
    sm = SessionManager(terminal, verbose=verbose)
    if session:
        sm.start_background_keepalive(interval=2.0)
    try:
        for (tx_id, pid), params in groups.items():
            if session and not sm.has_session(tx_id):
                await sm.open_session(tx_id, wake=wake)

            await sm.keepalive_stale()
            echo = request_echo(pid)
            response, _wall, _held = await timed_exchange(
                sm,
                tx_id,
                pid,
                retries=1,
                expected_sid=echo[0] if echo else None,
                expected_echo=echo[1] if echo else None,
            )
            if response.get("ok") or response.get("nrc") is not None:
                sm.mark_active(tx_id)

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
            rows = decode_param_rows(response["hex"], param_defs)
            all_results.extend(mask_rows(rows, {"parameters": param_defs}))
    finally:
        sm.stop_background_keepalive()

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
