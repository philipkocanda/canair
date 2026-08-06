"""Dispatch for the plain read surfaces: identity, one signal, one ECU, raw hex.

Read-only single requests. Each resolves its ECU/PID arguments, reports a clean
error for an unresolvable one, and hands off to its mode.
"""

from __future__ import annotations

import sys

from canlib.modes import mode_ecu, mode_identity, mode_param, mode_raw
from canlib.transport.protocol import Terminal


async def handle_identity(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    from canlib.ecus import resolve_tx

    tx_id = resolve_tx(args.tx)
    if tx_id is None:
        print(
            f"Error: could not resolve ECU '{args.tx}' "
            "(use a name like IGPM or a hex TX id like 770)",
            file=sys.stderr,
        )
        sys.exit(1)
    from canlib.quirks import resolve_quirks

    await mode_identity(
        terminal,
        tx_id,
        session=args.session,
        wake=args.wake,
        as_json=args.json,
        protocol=getattr(args, "protocol", "auto"),
        quirks=resolve_quirks(pids_data),
    )


async def handle_param(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    await mode_param(
        terminal,
        pids_data,
        args.param,
        args.verbose,
        args.json,
        session=args.session,
        wake=args.wake,
    )


async def handle_ecu(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    await mode_ecu(
        terminal,
        pids_data,
        args.ecu,
        args.pid,
        args.verbose,
        args.json,
        session=args.session,
        wake=args.wake,
    )


async def handle_raw(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    await mode_raw(
        terminal,
        args.raw,
        args.verbose,
        args.json,
        session=args.session,
        hold=args.hold,
        wake=args.wake,
        save=args.save,
        pids_data=pids_data,
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
    )
