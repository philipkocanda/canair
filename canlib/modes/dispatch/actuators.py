"""Dispatch for the surfaces that can actuate hardware: IOControl and routines.

Both have a read-only listing/TUI path and an execute path. ``routines --sf start``
sends startRoutine, which may move something, so it confirms first. The KWP2000
split lives in the scan families, not here.
"""

from __future__ import annotations

import sys

from canlib.modes.iocontrol import mode_iocontrol_execute
from canlib.modes.routines import mode_routines_execute
from canlib.transport.protocol import Terminal


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
        )
    else:
        from canlib.modes.iocontrol import mode_iocontrol_tui

        await mode_iocontrol_tui(
            terminal,
            pids_data,
            args.iocontrol,
            verbose=args.verbose,
            poll=getattr(args, "poll", False),
        )


async def handle_routines(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if args.rid:
        from canlib.modes.routines import SF_RESULTS, SF_START, SF_STOP

        sf_map = {"results": SF_RESULTS, "start": SF_START, "stop": SF_STOP}
        sf_name = (args.sf or "results").lower()
        if sf_name not in sf_map:
            print(
                f"Error: --sf must be one of: results, start, stop (got {args.sf!r})",
                file=sys.stderr,
            )
            sys.exit(1)
        sub_function = sf_map[sf_name]
        if sub_function == SF_START:
            print(
                f"!! WARNING: --sf start will send startRoutine (SF 0x01) to {args.routines} RID {args.rid}.",
                file=sys.stderr,
            )
            print(
                "!! This may actuate hardware. Continue? [y/N] ",
                end="",
                flush=True,
                file=sys.stderr,
            )
            answer = sys.stdin.readline().strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.", file=sys.stderr)
                sys.exit(0)
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
        from canlib.modes.routines import mode_routines_tui

        await mode_routines_tui(
            terminal,
            pids_data,
            args.routines,
            verbose=args.verbose,
        )
