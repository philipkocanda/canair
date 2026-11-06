"""Send a raw UDS request."""

import asyncio
import functools
import sys

from canair.captures.metadata import resolve_metadata
from canair.privacy.sensitive import MASK, masked_response, request_hidden
from canair.profile.model import Profile
from canair.query.steps import parse_raw_spec
from canair.render import messages
from canair.render.formatting import format_raw_with_bnn, print_hexdump, print_json_result
from canair.transport.stats import client_transport
from canair.uds.describe import decode_uds_response
from canair.uds.response import UdsResponse

from ...captures.build import build_raw_session, suggest_raw_label
from ...captures.oneshot import save_session_journaled
from ...ecus.names import ecu_name
from ...ecus.registry import rx_addr_str
from ...transport.protocol import Terminal


async def mode_raw(
    terminal: Terminal,
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
    *,
    profile: Profile,
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
    try:
        tx_id, service_pid = parse_raw_spec(raw_spec)
    except ValueError as e:
        messages.error(str(e))
        raise SystemExit(2) from None

    if hold or wake:
        session = True

    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}", file=sys.stderr if as_json else None)

    save_result = functools.partial(
        _save_raw,
        tx_id,
        service_pid,
        pids_data=pids_data,
        label=label,
        vehicle_states=vehicle_states,
        notes=notes,
        transport=client_transport(terminal),
        profile=profile,
    )

    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        response = await terminal.send_uds(service_pid, retries=1)
        hidden = response["ok"] and request_hidden(tx_id, service_pid, profile=profile)

        if as_json:
            print_json_result(masked_response(response) if hidden else response)
            if not hold:
                if save:
                    save_result(response)
                return
        elif not response["ok"]:
            error = response.get("error") or response.get("nrc_desc", "unknown error")
            if response.get("nrc") is not None:
                print(f"  NRC: 0x{response['nrc']:02X} -- {response['nrc_desc']}")
                print(f"  Service: 0x{response.get('nrc_service', 0):02X}")
            else:
                messages.error(str(error))
            if not hold:
                if save:
                    save_result(response)
                return
        elif hidden:
            print(f"  Response: {MASK}  (sensitive — read --reveal to show)")
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
            human = sys.stderr if as_json else sys.stdout
            print(file=human)
            print("  Session held open (TesterPresent keepalive active).", file=human)
            print("  Press Ctrl+C to release.", file=human)
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n  Releasing session…", file=human)
        elif save:
            save_result(response)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass


def _save_raw(
    tx_id: int,
    request: str,
    response: UdsResponse,
    pids_data: dict | None,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
    transport: str | None = None,
    *,
    profile: Profile,
) -> None:
    """Prompt (or use provided metadata) and save a raw request result to captures."""

    ecu = ecu_name(tx_id, profile=profile)
    suggested = suggest_raw_label(ecu, request)
    meta = resolve_metadata(label, vehicle_states, notes, suggested_label=suggested)
    if meta:
        label, vehicle_states, notes = meta
        session_dict = build_raw_session(
            ecu_ref=rx_addr_str(tx_id, profile=profile),
            tx_id=tx_id,
            request=request,
            response=response,
            label=label,
            vehicle_states=vehicle_states,
            notes=notes,
            pids_data=pids_data,
            transport=transport,
        )
        save_session_journaled(session_dict, profile=profile)
