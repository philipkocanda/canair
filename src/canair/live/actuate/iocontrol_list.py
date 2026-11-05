"""``canair io ECU --json``: list an ECU's IOControl DIDs from the profile (no device needed)."""

from __future__ import annotations

import json

from canair.core.terminal import terminal_columns, truncate_text
from canair.ecus.pids import build_iocontrol_index
from canair.live.actuate.not_found import report_not_found
from canair.render import ansi, glyphs
from canair.safety.actuation import on_refusal


def mode_iocontrol_list(pids_data: dict, ecu_name: str, as_json: bool = False) -> bool:
    """List all IOControl DIDs for an ECU (no CAN connection needed).

    Returns False, after an ``error:`` on stderr, when the ECU has no IOControl entries. With
    ``as_json`` stdout holds the JSON document or nothing.
    """
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        if available:
            report_not_found(
                f"no IOControl DIDs for ECU: {ecu_name}", available, "ECUs with IOControl"
            )
        else:
            report_not_found("no IOControl DIDs defined in any ECU file", [], "")
        return False

    ecu_info = ioctrl_index[ecu_key]
    cmds = ecu_info["cmds"]

    if as_json:
        out = {
            "ecu": ecu_key,
            "tx_id": f"0x{ecu_info['tx_id']:03X}",
            "iocontrol": {
                did: {
                    "label": c["label"],
                    "on": c["on"],
                    "off": c["off"],
                    "release": c["release"],
                    "on_refused": on_refusal(c["on"], c["release"], ecu_info["protocol"]),
                    "session": c["session"],
                    "session_mode": c["session_mode"],
                    "hold": c["hold"],
                    "verified": c["verified"],
                    "status_param": c.get("status_param"),
                }
                for did, c in cmds.items()
            },
        }
        print(json.dumps(out, indent=2))
        return True

    title = ansi.c(ecu_key, ansi.BOLD + ansi.CYAN)
    print(f"\n  {title} — TX 0x{ecu_info['tx_id']:03X} — {len(cmds)} IOControl DIDs\n")

    term_w = terminal_columns()
    did_w = max(len(d) for d in cmds) if cmds else 4
    label_w = max(len(c["label"]) for c in cmds.values()) if cmds else 5
    on_w = max(len(c["on"]) for c in cmds.values()) if cmds else 2
    off_w = max(len(c["off"]) for c in cmds.values()) if cmds else 3
    sp_w = max((len(c.get("status_param") or "") for c in cmds.values()), default=0)
    sp_w = max(sp_w, len("Read PID"))
    fixed_w = 2 + did_w + 2 + label_w + 2 + on_w + 2 + off_w + 2 + 2 + len("Verified  Hold")
    sp_w = min(sp_w, 100, max(len("Read PID"), term_w - fixed_w))

    hdr = (
        f"  {'DID':<{did_w}}  {'Label':<{label_w}}  {'ON cmd':<{on_w}}  "
        f"{'OFF cmd':<{off_w}}  {'Read PID':<{sp_w}}  Verified  Hold"
    )
    print(ansi.c(hdr, ansi.DIM))
    print(ansi.c(f"  {'─' * (len(hdr) - 2)}", ansi.DIM))

    for did, c in cmds.items():
        verified = ansi.c(glyphs.OK, ansi.GREEN) if c["verified"] else " "
        hold = "hold" if c["hold"] else ""
        sp = c.get("status_param") or ""
        print(
            f"  {ansi.c(f'{did:<{did_w}}', ansi.CYAN)}  {c['label']:<{label_w}}  "
            f"{c['on']:<{on_w}}  {c['off']:<{off_w}}  "
            f"{truncate_text(sp, sp_w):<{sp_w}}     {verified}      {hold}"
        )
    print()
    # The release is what exit and Ctrl-C send; it only needs naming where it is
    # not simply the OFF command (always the case on UDS).
    notes = []
    for did, c in cmds.items():
        refusal = on_refusal(c["on"], c["release"], ecu_info["protocol"])
        if refusal:
            notes.append(ansi.c(f"  {did}: ON refused — {refusal}", ansi.RED))
        elif c["release"] and c["release"] != c["off"].upper():
            notes.append(f"  {did}: released with {c['release']}")
    if notes:
        print("\n".join(notes) + "\n")
    return True
