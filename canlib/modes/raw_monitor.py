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

    from ..pids import EcuIndexEntry


def query_ecu_addresses(
    query_steps: list[dict], ecu_index: Mapping[str, EcuIndexEntry]
) -> dict[str, tuple[int, int]]:
    """name(upper) -> (tx_id, rx_id) for every known ECU in the query steps."""
    out: dict[str, tuple[int, int]] = {}
    for step in query_steps:
        ecu = step["ecu"].upper()
        info = ecu_index.get(ecu)
        if info:
            out[ecu] = (info["tx_id"], info["rx_id"])
    return out


def _keep_mode(args) -> str | None:
    from ..keepmode import keep_mode_from_args

    return keep_mode_from_args(args)


async def run_raw_monitor(args, host: str, port: int, bitrate: int, pids_data: dict) -> int:
    """Run the live monitor over raw CAN. Assumes the device is in slcan mode."""
    from ..pids import build_ecu_index
    from ..transport import RawUdsClient, SlcanTcpBus
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
    bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
    from ..timeouts import cli_timeout, ecu_timeouts_by_name

    cli = cli_timeout(args)
    client = RawUdsClient(
        bus,
        ecus,
        timeout=(cli if cli is not None else 3.0),
        ecu_timeouts=(None if cli is not None else ecu_timeouts_by_name(pids_data)),
        isotp_config=pids_data.get("isotp"),
    )
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
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
        raw_client=client,
    )
    if getattr(args, "timings", False):
        from ..timing import print_timings

        print_timings(client.timings, as_json=getattr(args, "json", False))
    return 0
