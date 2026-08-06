"""Dispatch for ``canair dtc``: read, clear, or scan every ECU's fault memory.

The only dispatch family with a **destructive** path: ``--clear`` erases stored
fault memory, so it confirms interactively unless ``--yes``. Read and scan-all are
read-only.
"""

from __future__ import annotations

import sys

from canlib.states import parse_states
from canlib.transport.protocol import Terminal


async def handle_dtc(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    from canlib.ecus import resolve_tx
    from canlib.modes.dtc import mode_dtc_clear, mode_dtc_read, mode_dtc_scan_all

    if getattr(args, "dtc_all", False):
        try:
            mask = int(str(args.mask).removeprefix("0x").removeprefix("0X"), 16)
        except ValueError:
            print(f"Error: --mask must be hex (e.g. FF), got {args.mask!r}", file=sys.stderr)
            sys.exit(1)
        await mode_dtc_scan_all(
            terminal,
            mask=mask,
            protocol=args.protocol,
            as_json=args.json,
            verbose=args.verbose,
            retry=getattr(args, "dtc_retry", True),
            log=getattr(args, "dtc_log", True),
            label=args.label,
            vehicle_states=parse_states(getattr(args, "state", None)),
        )
        return

    tx_id = resolve_tx(args.dtc)
    if tx_id is None:
        print(
            f"Error: could not resolve ECU '{args.dtc}' "
            "(use a name like BMS or a hex TX id like 7E4)",
            file=sys.stderr,
        )
        sys.exit(1)
    if getattr(args, "clear", False):
        try:
            group = int(str(args.group).removeprefix("0x").removeprefix("0X"), 16)
        except ValueError:
            print(f"Error: --group must be hex (e.g. FFFFFF), got {args.group!r}", file=sys.stderr)
            sys.exit(1)
        if not getattr(args, "yes", False):
            from canlib.ecus import ecu_display

            print(
                f"!! About to CLEAR DTCs on {ecu_display(tx_id)} "
                f"(group 0x{group & 0xFFFFFF:06X}). This erases stored fault memory.",
                file=sys.stderr,
            )
            print("!! Continue? [y/N] ", end="", flush=True, file=sys.stderr)
            answer = sys.stdin.readline().strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.", file=sys.stderr)
                return
        await mode_dtc_clear(
            terminal,
            tx_id,
            group=group,
            protocol=args.protocol,
            session=args.session,
            wake=args.wake,
            as_json=args.json,
            verbose=args.verbose,
            log=getattr(args, "dtc_log", True),
            label=args.label,
        )
    else:
        try:
            mask = int(str(args.mask).removeprefix("0x").removeprefix("0X"), 16)
        except ValueError:
            print(f"Error: --mask must be hex (e.g. FF), got {args.mask!r}", file=sys.stderr)
            sys.exit(1)
        await mode_dtc_read(
            terminal,
            tx_id,
            mask=mask,
            protocol=args.protocol,
            session=args.session,
            wake=args.wake,
            as_json=args.json,
            verbose=args.verbose,
            log=getattr(args, "dtc_log", True),
            label=args.label,
            vehicle_states=parse_states(getattr(args, "state", None)),
        )
