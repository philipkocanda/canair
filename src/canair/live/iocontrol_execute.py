"""``canair io ECU --did DID``: send one IOControl ON or OFF request, then hand the output back."""

from __future__ import annotations

import asyncio
import json
import sys

from canair.core.prompt import confirm
from canair.ecus.pids import build_iocontrol_index
from canair.live._iocontrol_actuate import send_iocontrol
from canair.live.status import format_status_value, query_param_status
from canair.safety.actuation import on_refusal
from canair.transport.protocol import Terminal


async def mode_iocontrol_execute(
    terminal: Terminal,
    pids_data: dict,
    ecu_name: str,
    did: str,
    off: bool = False,
    verbose: bool = False,
    as_json: bool = False,
    yes: bool = False,
):
    """Execute an IOControl ON or OFF command, after asking (``yes`` answers for the user)."""
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
    status_param = cmd_def.get("status_param")

    if not hex_cmd:
        print(f"  No {action} command defined for {ecu_key} {did_key} ({label})")
        return
    release_cmd = cmd_def["release"] or cmd_def["off"]
    if not off:
        refusal = on_refusal(hex_cmd, cmd_def["release"], ecu_info["protocol"])
        if refusal:
            # Nothing has been sent yet, so there is nothing to release.
            print(f"error: {ecu_key} {did_key} ON refused — {refusal}", file=sys.stderr)
            raise SystemExit(2)

    print(f"\n  {ecu_key} 0x{tx_id:03X} -- {label} -- {action}")
    print(f"  Command: {hex_cmd}")
    if status_param:
        print(f"  Readback PID: {status_param}")

    if not confirm(
        f"Send {action} to {ecu_key} {did_key} ({label})? This drives an output.",
        yes=yes,
        danger=True,
    ):
        raise SystemExit(1)

    await terminal.set_header(tx_id)

    tester_task = None
    if needs_session:
        mode = cmd_def["session_mode"]
        if verbose:
            print(f"  Entering diagnostic session (10 {mode})...")
        _, tester_task = await terminal.enter_extended_session(mode=mode)

    # Query the mapped readback PID before executing. This is a best-effort hint
    # only: IOControl overrides may drive an output without changing the ECU
    # status bit or register that the readback PID exposes.
    if status_param:
        before = await query_param_status(terminal, pids_data, status_param, verbose=verbose)
        await terminal.set_header(tx_id)  # restore header after status query may have changed it
        if before["error"]:
            print(f"  Readback before: ERR {before['error']}")
        else:
            print(f"  Readback before: {status_param} = {format_status_value(before)}")

    try:
        response = await send_iocontrol(terminal, hex_cmd)

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

        # Hand the output back if we were holding it ON: the entry's release
        # (returnControlToECU on UDS, the declared request on KWP2000), else OFF.
        if needs_hold and not off and release_cmd:
            print(f"  Releasing: {release_cmd}")
            await terminal.set_header(tx_id)
            release_resp = await send_iocontrol(terminal, release_cmd)
            if release_resp["ok"]:
                print(f"  ✓ Released: {release_resp['hex']}")
            else:
                error = release_resp.get("error") or f"NRC 0x{release_resp.get('nrc', 0):02X}"
                print(f"  ✗ Release failed: {error}")

        # Query the mapped readback PID after command/release. Same caveat as
        # above: it may reflect normal ECU logic rather than the temporary
        # IOControl override.
        if status_param:
            await terminal.set_header(tx_id)
            after = await query_param_status(terminal, pids_data, status_param, verbose=verbose)
            if after["error"]:
                print(f"  Readback after: ERR {after['error']}")
            else:
                print(f"  Readback after: {status_param} = {format_status_value(after)}")

    print()
