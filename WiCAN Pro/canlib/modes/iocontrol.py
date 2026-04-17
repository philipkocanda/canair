"""IOControl mode — list and execute IOControl commands from pids/ YAML."""

import asyncio
import json
import sys

from ..pids import build_iocontrol_index
from ..terminal import WiCANTerminal


def mode_iocontrol_list(pids_data: dict, ecu_name: str, as_json: bool = False):
    """List all IOControl DIDs for an ECU (no CAN connection needed)."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        if available:
            print(f"  No IOControl DIDs for ECU: {ecu_name}")
            print(f"  ECUs with IOControl: {', '.join(available)}")
        else:
            print("  No IOControl DIDs defined in any ECU file.")
        return

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
                    "session": c["session"],
                    "hold": c["hold"],
                    "verified": c["verified"],
                }
                for did, c in cmds.items()
            },
        }
        print(json.dumps(out, indent=2))
        return

    print(f"\n  {ecu_key} -- TX 0x{ecu_info['tx_id']:03X} -- {len(cmds)} IOControl DIDs\n")

    # Column-aligned table
    did_w = max(len(d) for d in cmds) if cmds else 4
    label_w = max(len(c["label"]) for c in cmds.values()) if cmds else 5
    on_w = max(len(c["on"]) for c in cmds.values()) if cmds else 2
    off_w = max(len(c["off"]) for c in cmds.values()) if cmds else 3

    hdr = f"  {'DID':<{did_w}}  {'Label':<{label_w}}  {'ON cmd':<{on_w}}  {'OFF cmd':<{off_w}}  Verified  Hold"
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    for did, c in cmds.items():
        v = "✓" if c["verified"] else " "
        h = "✓" if c["hold"] else " "
        print(
            f"  {did:<{did_w}}  {c['label']:<{label_w}}  {c['on']:<{on_w}}  {c['off']:<{off_w}}  "
            f"   {v}        {h}"
        )
    print()


async def mode_iocontrol_execute(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecu_name: str,
    did: str,
    off: bool = False,
    verbose: bool = False,
    as_json: bool = False,
):
    """Execute an IOControl ON or OFF command."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()
    did_key = did.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        print(f"  No IOControl DIDs for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with IOControl: {', '.join(available)}")
        return

    ecu_info = ioctrl_index[ecu_key]
    cmds = ecu_info["cmds"]

    if did_key not in cmds:
        available = sorted(cmds.keys())
        print(f"  Unknown DID {did_key} for {ecu_key}")
        if available:
            print(f"  Available DIDs: {', '.join(available)}")
        return

    cmd_def = cmds[did_key]
    tx_id = ecu_info["tx_id"]
    action = "OFF" if off else "ON"
    hex_cmd = cmd_def["off"] if off else cmd_def["on"]
    label = cmd_def["label"]
    needs_session = cmd_def["session"]
    needs_hold = cmd_def["hold"] and not off  # don't hold on OFF

    if not hex_cmd:
        print(f"  No {action} command defined for {ecu_key} {did_key} ({label})")
        return

    print(f"\n  {ecu_key} 0x{tx_id:03X} -- {label} -- {action}")
    print(f"  Command: {hex_cmd}")

    await terminal.set_header(tx_id)

    tester_task = None
    if needs_session:
        if verbose:
            print("  Entering extended diagnostic session (10 03)...")
        _, tester_task = await terminal.enter_extended_session()

    try:
        response = await terminal.send_uds(hex_cmd, timeout=3.0)

        if response["ok"]:
            print(f"  ✓ Positive response: {response['hex']}")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            print(f"  ✗ NRC 0x{nrc:02X}: {desc}")
        else:
            error = response.get("error", "unknown")
            print(f"  ✗ Error: {error}")

        if as_json:
            out = {
                "ecu": ecu_key,
                "did": did_key,
                "label": label,
                "action": action.lower(),
                "command": hex_cmd,
                "ok": response["ok"],
                "response": response["hex"] if response["ok"] else None,
                "nrc": f"0x{response['nrc']:02X}" if response.get("nrc") is not None else None,
            }
            print(json.dumps(out, indent=2))

        # Hold session if needed (keep TesterPresent alive until Ctrl+C)
        if needs_hold and response["ok"] and tester_task:
            print("\n  Holding session (Ctrl+C to release)...")
            try:
                await asyncio.Future()  # block forever
            except asyncio.CancelledError:
                pass

    except KeyboardInterrupt:
        print("\n  Releasing...")
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

        # Send OFF command on release if we were holding ON
        if needs_hold and not off and cmd_def["off"]:
            print(f"  Sending OFF: {cmd_def['off']}")
            release_resp = await terminal.send_uds(cmd_def["off"], timeout=3.0)
            if release_resp["ok"]:
                print(f"  ✓ Released: {release_resp['hex']}")
            else:
                error = release_resp.get("error") or f"NRC 0x{release_resp.get('nrc', 0):02X}"
                print(f"  ✗ Release failed: {error}")

    print()
