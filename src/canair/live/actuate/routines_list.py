"""``canair routines ECU --json``: list an ECU's routine RIDs from the profile (no device)."""

from __future__ import annotations

import json

from canair.core.terminal import terminal_columns, truncate_text
from canair.ecus.pids import build_routines_index
from canair.live.actuate.not_found import report_not_found
from canair.render import ansi, glyphs


def mode_routines_list(pids_data: dict, ecu_name: str, as_json: bool = False) -> bool:
    """List all routine RIDs for an ECU (no CAN connection needed).

    Returns False, after an ``error:`` on stderr, when the ECU has no routines. With ``as_json``
    stdout holds the JSON document or nothing.
    """
    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in rindex:
        available = sorted(rindex.keys())
        if available:
            report_not_found(
                f"no routines defined for ECU: {ecu_name}", available, "ECUs with routines"
            )
        else:
            report_not_found("no routines defined in any ECU file", [], "")
        return False

    ecu_info = rindex[ecu_key]
    routines = ecu_info["routines"]

    if as_json:
        out = {
            "ecu": ecu_key,
            "tx_id": f"0x{ecu_info['tx_id']:03X}",
            "routines": {
                rid: {
                    "label": r["label"],
                    "nrc": f"0x{r['nrc']:02X}" if r.get("nrc") is not None else None,
                    "nrc_desc": r["nrc_desc"],
                    "response": r["response"],
                    "verified": r["verified"],
                    "notes": r["notes"],
                }
                for rid, r in routines.items()
            },
        }
        print(json.dumps(out, indent=2))
        return True

    title = ansi.c(ecu_key, ansi.BOLD + ansi.CYAN)
    print(f"\n  {title} — TX 0x{ecu_info['tx_id']:03X} — {len(routines)} routine RIDs\n")

    term_w = terminal_columns()
    rid_w = max(len(r) for r in routines) if routines else 4
    label_w = max((len(r["label"]) for r in routines.values()), default=5)
    label_w = max(label_w, 5)
    scan_w = max((len(r["nrc_desc"] or r["response"] or "") for r in routines.values()), default=10)
    scan_w = max(scan_w, len("Scan result"))

    fixed_w = 2 + rid_w + 2 + label_w + 2 + scan_w + 2 + len("Verified")
    if fixed_w > term_w:
        scan_w = max(10, scan_w - (fixed_w - term_w))

    hdr = f"  {'RID':<{rid_w}}  {'Label':<{label_w}}  {'Scan result':<{scan_w}}  Verified"
    print(ansi.c(hdr, ansi.DIM))
    print(ansi.c(f"  {'─' * (len(hdr) - 2)}", ansi.DIM))

    for rid, r in routines.items():
        verified = ansi.c(glyphs.OK, ansi.GREEN) if r["verified"] else " "
        if r.get("nrc") is not None:
            scan_result = f"NRC 0x{r['nrc']:02X} {r['nrc_desc'] or ''}"
        elif r["response"]:
            scan_result = f"OK {r['response']}"
        else:
            scan_result = ""
        print(
            f"  {ansi.c(f'{rid:<{rid_w}}', ansi.CYAN)}  "
            f"{truncate_text(r['label'], label_w):<{label_w}}  "
            f"{truncate_text(scan_result, scan_w):<{scan_w}}     {verified}"
        )
    print()
    return True
