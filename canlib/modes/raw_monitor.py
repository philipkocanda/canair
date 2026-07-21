"""``canair query --monitor --raw-can`` — run the live monitor over raw CAN.

Uses the SLCAN-over-TCP backend + client-side ISO-TP (:class:`RawUdsClient`) with
**request pipelining** instead of the ELM327 WebSocket terminal. Requires the
WiCAN to be in ``slcan`` mode, so it switches with consent (`--yes`) and restores
the previous protocol on exit — one reboot each way (pauses ELM327/AutoPID).

Pipelining fires all PID requests for the cycle back-to-back across the ECUs'
ISO-TP stacks and collects the responses as they arrive, overlapping ECU
think-time (vs the strictly sequential request→wait of the ELM path).
"""

from __future__ import annotations

import sys


def query_ecu_addresses(query_steps: list[dict], ecu_index: dict) -> dict[str, tuple[int, int]]:
    """name(upper) -> (tx_id, rx_id) for every known ECU in the query steps."""
    from ..transport.uds_raw import response_id

    out: dict[str, tuple[int, int]] = {}
    for step in query_steps:
        ecu = step["ecu"].upper()
        info = ecu_index.get(ecu)
        if info:
            out[ecu] = (info["tx_id"], response_id(info["tx_id"]))
    return out


def _keep_mode(args) -> str | None:
    if getattr(args, "keep_unique", False):
        return "unique"
    if getattr(args, "keep_all", False):
        return "all"
    if getattr(args, "keep", None):
        return "last"
    return None


async def run_raw_monitor(args, host: str, pids_data: dict) -> int:
    # Port/bitrate come from the device config (reuses the sniff resolver).
    from ..commands.sniff import _resolve_device_defaults
    from ..pids import build_ecu_index
    from ..transport import RawUdsClient, SlcanTcpBus
    from ..wican_mode import ModeError, protocol_mode
    from .monitor import mode_monitor
    from .multi import parse_sub_commands

    commands = parse_sub_commands(args.multi)
    session_steps = [c for c in commands if c["type"] in ("session", "skm-wake", "sleep")]
    query_steps = [c for c in commands if c["type"] == "query"]
    if not query_steps:
        print("Error: --monitor requires at least one 'query' step in --multi", file=sys.stderr)
        return 1

    ecu_index = build_ecu_index(pids_data)
    ecus = query_ecu_addresses(query_steps, ecu_index)
    if not ecus:
        print("Error: no known ECUs in the query steps for --raw-can.", file=sys.stderr)
        return 1

    port, bitrate = _resolve_device_defaults(args.wican, None, None)
    print(
        f"  Raw CAN monitor via SLCAN — {host}:{port} @ {bitrate} bps  "
        f"(ECUs: {', '.join(sorted(ecus))})"
    )

    try:
        with protocol_mode(args.wican, "slcan", assume_yes=getattr(args, "yes", False)):
            bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
            client = RawUdsClient(bus, ecus, timeout=max(1.0, float(args.timeout)))
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
                state=args.state,
                notes=args.notes,
                raw_client=client,
            )
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0
