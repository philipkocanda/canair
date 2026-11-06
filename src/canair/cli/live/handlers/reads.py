"""Handlers for the plain read surfaces: identity, named signals, raw hex.

Read-only single requests. Each resolves its ECU/PID arguments, reports a clean
error for an unresolvable one, and hands off to its mode.
"""

from __future__ import annotations

from canair.live.identity.read import mode_identity
from canair.live.read.param import mode_param
from canair.live.read.raw import mode_raw
from canair.profile import active
from canair.render import messages
from canair.transport.protocol import Terminal


async def handle_identity(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    from canair.ecus.names import resolve_tx

    tx_id = resolve_tx(args.tx)
    if tx_id is None:
        messages.fail(
            f"could not resolve ECU '{args.tx}' (use a name like IGPM or a hex TX id like 770)"
        )
    await mode_identity(
        terminal,
        tx_id,
        session=args.session,
        wake=args.wake,
        as_json=args.json,
        protocol=getattr(args, "protocol", "auto"),
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
        profile=active(),
    )
