"""Raw-CAN command dispatch (transport = ``slcan-tcp``).

When the configured transport is raw, live commands run over python-can + our
client-side ISO-TP (:class:`RawUdsClient`) instead of the ELM327 WebSocket. The
device must already be in ``slcan`` mode — we verify and error clearly, never
switch. Supported: ``query`` (multi), ``raw`` (single request), ``monitor``.
Other modes (io/routines/discover/identity/scan/*-scan) aren't wired to the raw
path yet and return a clear error.
"""

from __future__ import annotations

import asyncio
import sys
import time


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
        require_protocol(host, "slcan")
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    port, bitrate = _resolve_device_defaults(host, transport.port, transport.bitrate)

    if args.multi and args.monitor:
        from .raw_monitor import run_raw_monitor

        return await run_raw_monitor(args, host, port, bitrate, pids_data)
    if args.multi:
        return await _raw_query(args, host, port, bitrate, pids_data)
    if args.raw:
        return await _raw_single(args, host, port, bitrate, pids_data)

    print(
        "error: this command isn't supported over the raw 'slcan-tcp' transport yet "
        "(supported: query, raw, monitor). Use transport 'wican-ws' for it.",
        file=sys.stderr,
    )
    return 2


def _ecu_addresses(steps, ecu_index):
    from ..transport.uds_raw import response_id

    out = {}
    for s in steps:
        e = s["ecu"].upper()
        if e in ecu_index:
            out[e] = (ecu_index[e]["tx_id"], response_id(ecu_index[e]["tx_id"]))
    return out


async def _raw_query(args, host, port, bitrate, pids_data) -> int:
    """One-shot pipelined read of the query steps, printed like the ELM path."""
    from ..formatting import print_ecu_results
    from ..pids import build_ecu_index
    from ..transport import RawUdsClient, SlcanTcpBus
    from .monitor import _raw_pid_result
    from .multi import build_query_plan, parse_sub_commands

    steps = [c for c in parse_sub_commands(args.multi) if c["type"] == "query"]
    idx = build_ecu_index(pids_data)
    ecus = _ecu_addresses(steps, idx)
    if not ecus:
        print("error: no known ECUs in the query.", file=sys.stderr)
        return 1

    print(f"  Raw CAN via SLCAN — {host}:{port} @ {bitrate} bps")
    bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
    client = RawUdsClient(bus, ecus, timeout=max(1.0, float(args.timeout)))
    loop = asyncio.get_event_loop()
    try:
        for s in steps:
            ecu = s["ecu"].upper()
            info = idx.get(ecu)
            if info is None:
                continue
            plan = build_query_plan(info, s.get("pids", []), quiet=False)
            if not plan:
                continue
            reqs = [(ecu, bytes.fromhex(code)) for code, _pi, _un in plan]
            results = await loop.run_in_executor(None, client.poll, reqs)
            acquired = time.time()
            pid_results = [
                _raw_pid_result(code, pi, un, results.get((ecu, bytes.fromhex(code))), acquired)
                for code, pi, un in plan
            ]
            print_ecu_results(
                ecu_label=f"{ecu} (0x{info['tx_id']:03X})",
                pid_results=pid_results,
                verbose=args.verbose,
            )
    finally:
        client.close()
    return 0


async def _raw_single(args, host, port, bitrate, pids_data) -> int:
    """Single raw UDS request (``TX:PID``) over the raw transport."""
    from ..formatting import decode_uds_response
    from ..transport import RawUdsClient, SlcanTcpBus, response_id

    spec = args.raw
    tx_hex, sep, pid_hex = spec.partition(":")
    if not sep:
        print(f"error: raw request must be TX:PID (e.g. 770:22BC03), got '{spec}'", file=sys.stderr)
        return 2
    try:
        tx = int(tx_hex, 16)
        req = bytes.fromhex(pid_hex)
    except ValueError:
        print(f"error: invalid hex in '{spec}'", file=sys.stderr)
        return 2

    print(f"  Raw CAN via SLCAN — {host}:{port} @ {bitrate} bps")
    bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
    client = RawUdsClient(bus, {"ECU": (tx, response_id(tx))}, timeout=max(2.0, float(args.timeout)))
    loop = asyncio.get_event_loop()
    try:
        try:
            resp = await loop.run_in_executor(None, client.read, "ECU", req)
        except TimeoutError:
            print(f"  TX: 0x{tx:03X}  Request: {pid_hex.upper()}")
            print("  Error: No response from ECU")
            return 1
        print(f"  TX: 0x{tx:03X}  Request: {pid_hex.upper()}")
        print(f"  Response: {resp.hex().upper()}")
        print(f"  {decode_uds_response(resp)}")
    finally:
        client.close()
    return 0
