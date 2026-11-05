"""Handlers for the multi mini-language surfaces: ``read``/``monitor`` steps.

The two entry points that take positional STEPs — a one-shot run, and the live
monitor. The monitor is the only handler that uses ``reconnect``.
"""

from __future__ import annotations

from canair.captures.keepmode import resolve_keep_mode
from canair.cli.args.notation import resolve_notation
from canair.cli.live.operation import LiveOperation
from canair.live._monitor_run import mode_monitor
from canair.live.monitor_options import MonitorOptions
from canair.live.poll_schedule import DEFAULT_SCHEDULE
from canair.live.read.multi import mode_multi
from canair.transport.protocol import Terminal


def monitor_options(args) -> MonitorOptions:
    """The monitor's options from the parsed flags (``args.multi`` already holds a query step)."""
    from canair.live.read.multi import parse_sub_commands

    commands = parse_sub_commands(args.multi)
    return MonitorOptions(
        query_steps=[c for c in commands if c["type"] == "query"],
        session_steps=[c for c in commands if c["type"] in ("session", "skm-wake", "sleep")],
        interval=args.interval,
        keep_mode=resolve_keep_mode(
            keep=getattr(args, "keep", None), keep_changes=getattr(args, "keep_changes", False)
        ),
        keep_n=getattr(args, "keep", None),
        save=args.save,
        show_rulers=getattr(args, "rulers", False),
        notation=resolve_notation(getattr(args, "notation", None)),
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
        include_static=getattr(args, "include_static", False),
        schedule=getattr(args, "schedule", DEFAULT_SCHEDULE),
        backoff=not getattr(args, "no_backoff", False),
        verbose=args.verbose,
        interactive=_run_monitor_app,
    )


async def _run_monitor_app(controller) -> None:
    """Run the Textual monitor on ``controller`` (a TTY only)."""
    # Deferred: Textual takes tens of milliseconds to import, and only a TTY monitor needs it.
    from canair.tui.apps.monitor import run_monitor_app

    await run_monitor_app(controller)


def monitor_operation(options: MonitorOptions) -> LiveOperation:
    """The ``monitor`` operation for ``options``: polled over a terminal, or the raw client."""

    async def handle_monitor(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
        await mode_monitor(
            terminal,
            options.query_steps,
            pids_data,
            options.verbose,
            reconnect=reconnect,
            **options.mode_kwargs(),
        )

    return LiveOperation(handle_monitor, monitor=options, records=True)


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
        as_json=getattr(args, "json", False),
    )
