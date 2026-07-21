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
    from ..commands.sniff import _resolve_device_defaults
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

    port, bitrate = _resolve_device_defaults(host, transport.port, transport.bitrate)

    # Monitor: optimized pipelined + batched backend.
    if args.multi and args.monitor:
        from .raw_monitor import run_raw_monitor

        return await run_raw_monitor(args, host, port, bitrate, pids_data)

    # All other commands: reuse the shared dispatch over a RawTerminal adapter.
    from ..commands._live import dispatch_mode
    from ..transport import RawTerminal

    print(f"  Raw CAN via SLCAN — {host}:{port} @ {bitrate} bps")
    terminal = RawTerminal(
        host, port, bitrate, verbose=args.verbose, unsafe=getattr(args, "unsafe", False)
    )
    try:
        await dispatch_mode(args, terminal, pids_data, host)
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1
    finally:
        await terminal.close()
    return 0
