"""Dispatch for the multi mini-language surfaces: ``read``/``monitor`` steps.

The two entry points that take positional STEPs — a one-shot run, and the live
monitor. The monitor is the only handler that uses ``reconnect``.
"""

from __future__ import annotations

import sys

from canlib.keepmode import keep_mode_from_args
from canlib.modes import mode_monitor, mode_multi
from canlib.notation import resolve_notation
from canlib.transport.protocol import Terminal


async def handle_monitor(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    from canlib.modes.multi import parse_sub_commands

    commands = parse_sub_commands(args.multi)
    session_steps = [c for c in commands if c["type"] in ("session", "skm-wake", "sleep")]
    query_steps = [c for c in commands if c["type"] == "query"]
    if not query_steps:
        print(
            "Error: monitor requires at least one 'query' step",
            file=sys.stderr,
        )
        sys.exit(1)
    await mode_monitor(
        terminal,
        query_steps,
        pids_data,
        args.verbose,
        interval=args.monitor,
        session_steps=session_steps,
        keep_mode=keep_mode_from_args(args),
        keep_n=args.keep,
        save=args.save,
        show_rulers=args.rulers,
        notation=resolve_notation(getattr(args, "notation", None)),
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
        include_static=getattr(args, "include_static", False),
        reconnect=reconnect,
    )


async def handle_multi(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    await mode_multi(
        terminal,
        args.multi,
        pids_data,
        args.verbose,
        no_repl=not args.repl,
        save=args.save,
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
        include_static=getattr(args, "include_static", False),
    )
