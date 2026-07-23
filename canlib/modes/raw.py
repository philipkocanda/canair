"""Send a raw UDS request."""

import asyncio
import re

from ..formatting import decode_uds_response, format_raw_with_bnn, print_hexdump, print_json_result
from ..terminal import WiCANTerminal


async def mode_raw(
    terminal: WiCANTerminal,
    raw_spec: str,
    verbose: bool,
    as_json: bool,
    session: bool = False,
    hold: bool = False,
    wake: bool = False,
    save: bool = False,
    pids_data: dict | None = None,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
):
    """Send a raw UDS request specified as TX_ID:SERVICE_PID.

    Args:
        hold: If True, keep the extended diagnostic session alive after the
            command completes (TesterPresent keepalive runs until Ctrl+C).
            Useful for IOControl (2F) commands where the actuator releases
            when the session drops. Implies --session.
        save: If True, prompt for metadata and save result to captures/.
        pids_data: Loaded PID definitions for decoding (used with --save).
    """
    match = re.match(r"^([0-9A-Fa-f]{3})[:\s]([0-9A-Fa-f]+)$", raw_spec)
    if not match:
        print(f"  Invalid format: {raw_spec}")
        print("  Expected: <TX_ID>:<SERVICE_PID>  (e.g., 7E4:2101)")
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
        response = await terminal.send_uds(service_pid, retries=1)

        if as_json:
            print_json_result(response)
            if not hold:
                if save:
                    _save_raw(tx_id, service_pid, response, pids_data, label, vehicle_states, notes)
                return
        elif not response["ok"]:
            error = response.get("error") or response.get("nrc_desc", "unknown error")
            if response.get("nrc") is not None:
                print(f"  NRC: 0x{response['nrc']:02X} -- {response['nrc_desc']}")
                print(f"  Service: 0x{response.get('nrc_service', 0):02X}")
            else:
                print(f"  Error: {error}")
            if not hold:
                if save:
                    _save_raw(tx_id, service_pid, response, pids_data, label, vehicle_states, notes)
                return
        else:
            decode = decode_uds_response(response["bytes"])
            if decode:
                print(f"  → {decode}")
                print(f"    Raw: {response['hex']}")
                print(f"    Bnn: {format_raw_with_bnn(response['bytes'])}")
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
        elif save:
            _save_raw(tx_id, service_pid, response, pids_data, label, vehicle_states, notes)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass


def _save_raw(
    tx_id: int, request: str, response: dict, pids_data: dict | None,
    label: str | None = None, vehicle_states=None, notes: str | None = None,
) -> None:
    """Prompt (or use provided metadata) and save a raw request result to captures."""
    from ..captures import (
        build_raw_session,
        resolve_metadata,
        save_session_journaled,
        suggest_raw_label,
    )
    from ..ecus import ecu_name, rx_addr_str

    ecu = ecu_name(tx_id)
    suggested = suggest_raw_label(ecu, request)
    meta = resolve_metadata(label, vehicle_states, notes, suggested_label=suggested)
    if meta:
        label, vehicle_states, notes = meta
        session_dict = build_raw_session(
            ecu_ref=rx_addr_str(tx_id),
            tx_id=tx_id,
            request=request,
            response=response,
            label=label,
            vehicle_states=vehicle_states,
            notes=notes,
            pids_data=pids_data,
        )
        save_session_journaled(session_dict)
