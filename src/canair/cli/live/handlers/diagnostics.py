"""Handlers for ``canair dtc``: read, clear, or scan every ECU's fault memory.

The only handler family with a **destructive** path: ``--clear`` erases stored
fault memory, so it confirms interactively unless ``--yes``. Read and scan-all are
read-only.
"""

from __future__ import annotations

from canair.cli.profile_context import active
from canair.core.prompt import confirm
from canair.ecus.names import ecu_display, resolve_tx
from canair.live.dtc import mode_dtc_clear, mode_dtc_read, mode_dtc_scan_all, resolve_protocol
from canair.live.dtc.advice import power_state_note
from canair.render import messages
from canair.states.tokens import parse_states
from canair.transport.protocol import Terminal
from canair.uds.protocol import KWP
from canair.uds.requests import dtc_group_width


async def handle_dtc(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if getattr(args, "dtc_all", False):
        try:
            mask = int(str(args.mask).removeprefix("0x").removeprefix("0X"), 16)
        except ValueError:
            messages.fail(f"--mask must be hex (e.g. FF), got {args.mask!r}", code=2)
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
            snapshots=getattr(args, "dtc_snapshots", True),
            pids_data=pids_data,
            save=getattr(args, "save", False),
            notes=getattr(args, "notes", None),
            profile=active(),
        )
        return

    tx_id = resolve_tx(args.dtc, profile=active())
    if tx_id is None:
        messages.fail(
            f"could not resolve ECU '{args.dtc}' (use a name like BMS or a hex TX id like 7E4)"
        )
    if getattr(args, "clear", False):
        proto = resolve_protocol(args.protocol, tx_id, profile=active())
        group = _clear_group(args.group, kwp=proto == KWP)
        width = dtc_group_width(kwp=proto == KWP)
        what = "every DTC" if group is None else f"DTC group 0x{group:0{2 * width}X}"
        ecu = ecu_display(tx_id, profile=active())
        messages.warning("clearing DTCs erases the stored fault memory")
        if note := power_state_note(tx_id, profile=active(), pids_data=pids_data):
            messages.note(note)
        if not confirm(
            f"Clear {what} on {ecu}?",
            yes=getattr(args, "yes", False),
            danger=True,
        ):
            raise SystemExit(1)
        cleared = await mode_dtc_clear(
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
            snapshots=getattr(args, "dtc_snapshots", True),
            pids_data=pids_data,
            profile=active(),
        )
        if not cleared:
            raise SystemExit(1)
    else:
        try:
            mask = int(str(args.mask).removeprefix("0x").removeprefix("0X"), 16)
        except ValueError:
            messages.fail(f"--mask must be hex (e.g. FF), got {args.mask!r}", code=2)
        supported = getattr(args, "dtc_supported", False)
        if supported and resolve_protocol(args.protocol, tx_id, profile=active()) != KWP:
            messages.fail(
                f"--supported needs a KWP2000 ECU, but {args.dtc} is not one "
                "(see `canair ecu`; --protocol kwp forces it)",
                code=2,
            )
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
            supported=supported,
            snapshots=getattr(args, "dtc_snapshots", True),
            pids_data=pids_data,
            save=getattr(args, "save", False),
            notes=getattr(args, "notes", None),
            profile=active(),
        )


def _clear_group(text: str | None, *, kwp: bool) -> int | None:
    """``--group`` as an int that fits the protocol's width, or None (every DTC)."""
    if text is None:
        return None
    try:
        group = int(str(text).removeprefix("0x").removeprefix("0X"), 16)
    except ValueError:
        messages.fail(f"--group must be hex (e.g. FFFFFF), got {text!r}", code=2)
    width = dtc_group_width(kwp=kwp)
    if not 0 <= group < 1 << (8 * width):
        protocol = "KWP2000" if kwp else "UDS"
        messages.fail(
            f"--group 0x{group:X} does not fit the {width}-byte group of a {protocol} ECU", code=2
        )
    return group
