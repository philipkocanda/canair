"""``canair routines ECU --rid RID``: send one RoutineControl sub-function and print the reply."""

from __future__ import annotations

import json
import sys

from canair.ecus.pids import build_routines_index
from canair.live.actuate.not_found import report_not_found
from canair.render import ansi, glyphs
from canair.render.paint import Painter
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.requests import routine_control_request
from canair.uds.services import ROUTINE_CONTROL
from canair.uds.subfunctions import RoutineControlType, subfunction_name


def sf_label(sf: int) -> str:
    return subfunction_name(ROUTINE_CONTROL, sf) or f"0x{sf:02X}"


async def mode_routines_execute(
    terminal: Terminal,
    pids_data: dict,
    ecu_name: str,
    rid: str,
    sub_function: int = RoutineControlType.REQUEST_ROUTINE_RESULTS,
    verbose: bool = False,
    as_json: bool = False,
):
    """Execute a RoutineControl sub-function on a specific RID.

    Defaults to REQUEST_ROUTINE_RESULTS (0x03, read-only). START_ROUTINE sends startRoutine;
    the caller is responsible for confirming that is intentional. A lookup that finds nothing is
    ``error:`` on stderr and exit 1. With ``as_json`` stdout holds only the JSON document: the
    report of what was sent goes to stderr.
    """
    stream = sys.stderr if as_json else sys.stdout
    paint = Painter(stream)

    def say(text: str = "") -> None:
        print(text, file=stream)

    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()
    rid_key = rid.upper()

    if ecu_key not in rindex:
        report_not_found(f"no routines for ECU: {ecu_name}", rindex.keys(), "ECUs with routines")
        raise SystemExit(1)

    ecu_info = rindex[ecu_key]
    routines = ecu_info["routines"]

    if rid_key not in routines:
        report_not_found(f"unknown RID {rid_key} for {ecu_key}", routines.keys(), "available RIDs")
        raise SystemExit(1)

    rdef = routines[rid_key]
    tx_id = ecu_info["tx_id"]
    sf_name = sf_label(sub_function)
    label = rdef["label"] or rid_key

    hex_cmd = routine_control_request(sub_function, int(rid_key, 16))

    say(
        f"\n  {paint(ecu_key, ansi.raw.BOLD + ansi.raw.CYAN)} 0x{tx_id:03X} — "
        f"{label} ({paint(rid_key, ansi.raw.CYAN)}) — {sf_name}"
    )
    say(f"  Command: {hex_cmd}")

    async with SessionManager(terminal, verbose=verbose).held(tx_id):
        response = await terminal.send_uds(hex_cmd, timeout=3.0)

        fail_mark = paint(glyphs.FAIL, ansi.raw.RED)
        if response["ok"]:
            say(f"  {paint(glyphs.OK, ansi.raw.GREEN)} Positive response: {response['hex']}")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            say(f"  {fail_mark} {paint(f'NRC 0x{nrc:02X}', ansi.raw.YELLOW)}: {desc}")
        else:
            say(f"  {fail_mark} {response.get('error', 'unknown error')}")

        if as_json:
            out = {
                "ecu": ecu_key,
                "rid": rid_key,
                "label": label,
                "sub_function": sf_name,
                "command": hex_cmd,
                "ok": response["ok"],
                "response": response["hex"] if response["ok"] else None,
                "nrc": f"0x{response['nrc']:02X}" if response.get("nrc") is not None else None,
            }
            print(json.dumps(out, indent=2))

    say()
