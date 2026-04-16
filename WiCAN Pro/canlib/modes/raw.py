"""Send a raw UDS request."""

import asyncio
import re

from ..terminal import WiCANTerminal
from ..formatting import print_hexdump, print_json_result


async def mode_raw(terminal: WiCANTerminal, raw_spec: str, verbose: bool, as_json: bool,
                   session: bool = False, hold: bool = False, wake: bool = False):
    """Send a raw UDS request specified as TX_ID:SERVICE_PID.

    Args:
        hold: If True, keep the extended diagnostic session alive after the
            command completes (TesterPresent keepalive runs until Ctrl+C).
            Useful for IOControl (2F) commands where the actuator releases
            when the session drops. Implies --session.
    """
    match = re.match(r"^([0-9A-Fa-f]{3})[:\s]([0-9A-Fa-f]+)$", raw_spec)
    if not match:
        print(f"  Invalid format: {raw_spec}")
        print(f"  Expected: <TX_ID>:<SERVICE_PID>  (e.g., 7E4:2101)")
        return

    tx_id = int(match.group(1), 16)
    service_pid = match.group(2).upper()

    if hold or wake:
        session = True

    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}")

    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        response = await terminal.send_uds(service_pid)

        if as_json:
            print_json_result(response)
            if not hold:
                return
        elif not response["ok"]:
            error = response.get("error") or response.get("nrc_desc", "unknown error")
            if response.get("nrc") is not None:
                print(f"  NRC: 0x{response['nrc']:02X} -- {response['nrc_desc']}")
                print(f"  Service: 0x{response.get('nrc_service', 0):02X}")
            else:
                print(f"  Error: {error}")
            if not hold:
                return
        else:
            print(f"  Response ({len(response['bytes'])} bytes): {response['hex']}")
            print()
            print_hexdump(response["bytes"])

        if hold and tester_task:
            print()
            print("  Session held open (TesterPresent keepalive active).")
            print("  Press Ctrl+C to release.")
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n  Releasing session...")
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
