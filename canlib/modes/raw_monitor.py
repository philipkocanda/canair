"""Live monitor over the raw CAN transport (SLCAN + client-side ISO-TP).

Uses :class:`RawUdsClient` with request pipelining + per-ECU multi-DID batching
instead of the ELM327 WebSocket terminal. The device must already be in ``slcan``
mode (the caller / ``run_raw`` verifies this — no auto-switching).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..addressing import EcuAddress
    from ..keepmode import KeepMode
    from ..pids import EcuIndexEntry


def query_ecu_addresses(
    query_steps: list[dict], ecu_index: Mapping[str, EcuIndexEntry]
) -> dict[str, EcuAddress]:
    """name(upper) -> resolved :class:`EcuAddress` for every ECU in the query steps."""
    out: dict[str, EcuAddress] = {}
    for step in query_steps:
        ecu = step["ecu"].upper()
        info = ecu_index.get(ecu)
        if info:
            out[ecu] = info["address"]
    return out


def _keep_mode(args) -> KeepMode:
    from ..keepmode import keep_mode_from_args

    return keep_mode_from_args(args)


def build_raw_client(
    host: str,
    port: int,
    bitrate: int,
    ecus: dict[str, EcuAddress],
    args,
    pids_data: dict,
):
    """Construct a connected :class:`RawUdsClient` (SLCAN bus + client-side ISO-TP).

    The single build path shared by the initial monitor connect and the
    mid-session reconnect, so both apply the same timeouts / ISO-TP config.
    Raises ``OSError`` if the SLCAN socket can't be opened.
    """
    from ..quirks import HK_F1XX_MINUS_ONE, has_quirk
    from ..timeouts import cli_timeout, ecu_timeouts_by_name
    from ..transport import RawUdsClient, SlcanTcpBus

    bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
    cli = cli_timeout(args)
    return RawUdsClient(
        bus,
        ecus,
        timeout=(cli if cli is not None else 3.0),
        ecu_timeouts=(None if cli is not None else ecu_timeouts_by_name(pids_data)),
        isotp_config=pids_data.get("isotp"),
        hk_f1xx_offset=has_quirk(pids_data, HK_F1XX_MINUS_ONE),
    )


def build_raw_reconnector(args, ecus: dict[str, EcuAddress], pids_data: dict):
    """A :class:`MonitorReconnector` that re-homes to a reachable slcan-tcp device.

    Restricts the candidate list to raw (slcan-tcp) devices and reuses
    :func:`build_raw_client` as the per-candidate connect step (built off-thread,
    since opening the SLCAN socket blocks).
    """
    import asyncio

    from ..transport import resolve_transport_candidates
    from ..transport.config import TransportConfig
    from .monitor_reconnect import MonitorReconnector, reconnect_policy

    candidates = [c for c in resolve_transport_candidates(args) if c.is_raw]

    async def connect(cand: TransportConfig):
        assert cand.host is not None  # wait_for_reachable only yields hosted candidates
        port, bitrate = cand.resolve_device_defaults(pids_data.get("can_bitrate"))
        return await asyncio.to_thread(
            build_raw_client, cand.host, port, bitrate, ecus, args, pids_data
        )

    return MonitorReconnector(candidates, connect, reconnect_policy(args))


async def run_raw_monitor(args, host: str, port: int, bitrate: int, pids_data: dict) -> int:
    """Run the live monitor over raw CAN. Assumes the device is in slcan mode."""
    from ..pids import build_ecu_index
    from .monitor import mode_monitor
    from .multi import parse_sub_commands

    commands = parse_sub_commands(args.multi)
    session_steps = [c for c in commands if c["type"] in ("session", "skm-wake", "sleep")]
    query_steps = [c for c in commands if c["type"] == "query"]
    if not query_steps:
        print("Error: monitor requires at least one 'query' step", file=sys.stderr)
        return 1

    ecu_index = build_ecu_index(pids_data)
    ecus = query_ecu_addresses(query_steps, ecu_index)
    if not ecus:
        print("Error: no known ECUs in the query steps.", file=sys.stderr)
        return 1

    print(
        f"  Raw CAN monitor via SLCAN — {host}:{port} @ {bitrate} bps  "
        f"(ECUs: {', '.join(sorted(ecus))})"
    )
    try:
        client = build_raw_client(host, port, bitrate, ecus, args, pids_data)
    except OSError as e:
        from ..transport.errors import connect_error_detail

        print(
            f"error: can't connect to the SLCAN port at {host}:{port} — {connect_error_detail(e)}",
            file=sys.stderr,
        )
        return 1
    # The monitor reconciles its --save journal in its own finally even on a
    # disconnect (so no data loss), then re-raises the transport error — catch it
    # here through the shared classifier so a dropped bus is a clean message, not
    # a traceback. (The ELM monitor gets this via dispatch_mode/run_session_guarded;
    # the raw monitor bypasses dispatch_mode for its pipelined client, so guard here.)
    from ..keepmode import wants_save
    from ..notation import resolve_notation
    from ..transport.errors import describe_transport_error, transport_error_types

    try:
        await mode_monitor(
            None,
            query_steps,
            pids_data,
            args.verbose,
            interval=args.monitor,
            session_steps=session_steps,
            keep_mode=_keep_mode(args),
            keep_n=getattr(args, "keep", None),
            save=args.save,
            show_rulers=getattr(args, "rulers", False),
            notation=resolve_notation(getattr(args, "notation", None)),
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
            raw_client=client,
            include_static=getattr(args, "include_static", False),
            reconnect=build_raw_reconnector(args, ecus, pids_data),
        )
    except transport_error_types() as e:
        print(
            "error: "
            + describe_transport_error(
                e, host=host, transport_label="SLCAN", saving=wants_save(args)
            ),
            file=sys.stderr,
        )
        return 1
    if getattr(args, "timings", False):
        from ..timing import print_timings

        print_timings(client.timings, as_json=getattr(args, "json", False))
    return 0
