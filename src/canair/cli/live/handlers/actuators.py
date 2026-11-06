"""Handlers for the surfaces that can actuate hardware: IOControl, routines, actions.

IOControl and routines have a read-only listing/TUI path and an execute path.
``routines --sf start`` sends startRoutine, which may move something, so it
confirms first. A profile action is always guarded by a live condition check and
a confirmation (:mod:`canair.live.actuate.action`). The KWP2000 split lives in the scan
families, not here.
"""

from __future__ import annotations

from canair.cli.profile_context import active
from canair.core.prompt import confirm
from canair.live.actuate.iocontrol import mode_iocontrol_tui
from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute
from canair.live.actuate.routines import mode_routines_tui
from canair.live.actuate.routines_execute import mode_routines_execute
from canair.render import messages
from canair.transport.protocol import Terminal
from canair.uds.subfunctions import RoutineControlType


async def handle_iocontrol(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if args.did:
        await mode_iocontrol_execute(
            terminal,
            pids_data,
            args.iocontrol,
            args.did,
            off=args.off,
            verbose=args.verbose,
            as_json=args.json,
            yes=getattr(args, "yes", False),
        )
    else:
        await mode_iocontrol_tui(
            terminal,
            pids_data,
            args.iocontrol,
            verbose=args.verbose,
            poll=getattr(args, "poll", False),
            profile=active(),
        )


async def handle_routines(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if args.rid:
        sf_map = {
            "results": RoutineControlType.REQUEST_ROUTINE_RESULTS,
            "start": RoutineControlType.START_ROUTINE,
            "stop": RoutineControlType.STOP_ROUTINE,
        }
        sf_name = (args.sf or "results").lower()
        if sf_name not in sf_map:
            messages.fail(f"--sf must be one of: results, start, stop (got {args.sf!r})", code=2)
        sub_function = sf_map[sf_name]
        if sub_function == RoutineControlType.START_ROUTINE:
            messages.warning(
                f"--sf start sends startRoutine (SF 0x01) to {args.routines} RID {args.rid}"
            )
            if not confirm(
                "This may actuate hardware. Continue?", yes=getattr(args, "yes", False), danger=True
            ):
                raise SystemExit(1)
        await mode_routines_execute(
            terminal,
            pids_data,
            args.routines,
            args.rid,
            sub_function=sub_function,
            verbose=args.verbose,
            as_json=args.json,
        )
    else:
        await mode_routines_tui(
            terminal,
            pids_data,
            args.routines,
            verbose=args.verbose,
            profile=active(),
        )


async def handle_action(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    """``canair action run NAME``: a guarded, profile-defined action (live/actuate/action)."""
    from canair.actions import ActionError
    from canair.live.actuate.action import mode_action

    try:
        await mode_action(
            terminal,
            pids_data,
            args.action,
            yes=getattr(args, "yes", False),
            dry_run=getattr(args, "dry_run", False),
            as_json=args.json,
            verbose=args.verbose,
            profile=active(),
        )
    except ActionError as e:
        messages.fail(str(e), code=2)
