"""Raw-CAN command dispatch (transport = ``slcan-tcp``).

When the configured transport is raw, live commands run over python-can + our
client-side ISO-TP instead of the ELM327 WebSocket. The device must already be
in ``slcan`` mode — we verify and error clearly, never switch.

- ``monitor`` uses the optimized :class:`RawUdsClient` path (request pipelining
  across ECUs + per-ECU multi-DID batching).
- Everything else (query, raw, scan, discover, identity, iocontrol, routines,
  and the ``*-scan`` probers) runs the normal ELM-path dispatch over a
  :class:`~canlib.transport.raw_terminal.RawTerminal` adapter, which speaks
  ISO-TP under the same ``set_header`` / ``send_uds`` interface the modes expect.
"""

from __future__ import annotations

import sys


async def run_raw(args, transport, pids_data) -> int:
    """Entry point for a live command over a raw (slcan-tcp) transport."""
    from ..wican_mode import ModeError, require_protocol

    host = transport.host
    if not host:
        print("error: transport has no host configured.", file=sys.stderr)
        return 2

    # Explicit-mode policy: the device must already be serving SLCAN.
    try:
        require_protocol(host, "slcan", transport_name="slcan-tcp")
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    port, bitrate = transport.resolve_device_defaults(pids_data.get("can_bitrate"))

    # Monitor: optimized pipelined + batched backend.
    if args.multi and args.monitor:
        from .raw_monitor import run_raw_monitor

        return await run_raw_monitor(args, host, port, bitrate, pids_data)

    # All other commands: reuse the shared dispatch over a RawTerminal adapter.
    from ..addressing import EcuAddress, resolve_mode, resolve_rx_offset
    from ..commands._live import dispatch_mode
    from ..pids import build_ecu_index
    from ..quirks import HK_F1XX_MINUS_ONE, has_quirk
    from ..timeouts import cli_timeout, ecu_timeouts_by_tx
    from ..transport import RawTerminal

    print(f"  Raw CAN via SLCAN — {host}:{port} @ {bitrate} bps")
    cli = cli_timeout(args)
    # Resolved tx->EcuAddress map (per-ECU mode/rx_id/extended/FC bytes) so
    # RawTerminal builds each ISO-TP stack from the full addressing; unknown TX
    # ids (a discovery sweep) fall back to the profile's rx_offset/mode.
    ecu_index = build_ecu_index(pids_data)
    addr_map: dict[int, EcuAddress] = {
        info["tx_id"]: info["address"] for info in ecu_index.values()
    }
    terminal = RawTerminal(
        host,
        port,
        bitrate,
        verbose=args.verbose,
        unsafe=getattr(args, "unsafe", False),
        timeout=(cli if cli is not None else 2.0),
        isotp_config=pids_data.get("isotp"),
        addr_map=addr_map,
        rx_offset=resolve_rx_offset(pids_data),
        mode=resolve_mode(pids_data),
        hk_f1xx_offset=has_quirk(pids_data, HK_F1XX_MINUS_ONE),
    )
    # Per-ECU budgets apply only when the user didn't force --timeout.
    if cli is None:
        terminal.ecu_timeouts = ecu_timeouts_by_tx(pids_data)
    try:
        await dispatch_mode(args, terminal, pids_data, host)
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1
    finally:
        if getattr(args, "timings", False):
            from ..timing import print_timings

            print_timings(terminal.timings, as_json=getattr(args, "json", False))
        await terminal.close()
    return 0
