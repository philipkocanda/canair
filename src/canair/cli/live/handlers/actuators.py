"""Handlers for the surfaces that change the car: IOControl, routines, actions, ECU resets.

IOControl and routines have a read-only listing/TUI path and an execute path.
``routines --sf start`` sends startRoutine, which may move something, so it
confirms first. ``routines`` builds UDS requests only, so it refuses a KWP2000
ECU before anything else
(:func:`canair.live.actuate.routines_execute.check_routines_protocol`), and asks first
about an ECU whose profile records no protocol (:func:`.reads.confirm_unknown_protocol`). A profile
action is always guarded by a live condition check and a confirmation
(:mod:`canair.live.actuate.action`). An ECU reset is refused by its rule
(:mod:`canair.safety.reset`) before the user is asked to type the ECU's name. The
``safety.allow_reset`` gate is the command's, checked before it connects. The KWP2000 scans live
in the scan families.
"""

from __future__ import annotations

from canair.actions import ActionError
from canair.cli.live.handlers.reads import confirm_unknown_protocol
from canair.cli.profile_context import active
from canair.core.prompt import confirm, typed_confirm
from canair.ecus.names import ecu_display, ecu_name, resolve_tx
from canair.live.actuate.action import mode_action
from canair.live.actuate.ecu_reset import mode_ecu_reset
from canair.live.actuate.iocontrol import mode_iocontrol_tui
from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute
from canair.live.actuate.routines import mode_routines_tui
from canair.live.actuate.routines_execute import check_routines_protocol, mode_routines_execute
from canair.live.hold import wake_plan
from canair.render import messages
from canair.safety.blocklist import SafetyBlocked
from canair.safety.reset import RESET_TYPES, reset_block, reset_type_name
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
            interactive=_run_iocontrol_app,
        )


async def _run_iocontrol_app(controller) -> None:
    """Run the Textual IOControl view on ``controller`` (a TTY only)."""
    # deferred: textual adds ~40 ms to start-up, and only the interactive view needs it
    from canair.tui.apps.iocontrol.app import run_iocontrol_app

    await run_iocontrol_app(controller)


async def handle_routines(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    # Before any question: a refused ECU is never asked about.
    check_routines_protocol(pids_data, args.routines)
    yes = getattr(args, "yes", False)
    sub_function = RoutineControlType.REQUEST_ROUTINE_RESULTS
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
    what = f"31 {sub_function:02X} {args.rid.upper()}" if args.rid else "the TUI's 31 … requests"
    if not confirm_unknown_protocol([args.routines], what, pids_data, yes=yes, profile=active()):
        raise SystemExit(1)
    if args.rid:
        if sub_function == RoutineControlType.START_ROUTINE:
            messages.warning(
                f"--sf start sends startRoutine (SF 0x01) to {args.routines} RID {args.rid}"
            )
            if not confirm("This may actuate hardware. Continue?", yes=yes, danger=True):
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
            interactive=_run_routines_app,
        )


async def _run_routines_app(controller) -> None:
    """Run the Textual routines view on ``controller`` (a TTY only)."""
    # deferred: textual adds ~40 ms to start-up, and only the interactive view needs it
    from canair.tui.apps.routines.app import run_routines_app

    await run_routines_app(controller)


async def handle_action(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    """``canair action run NAME``: a guarded, profile-defined action (live/actuate/action)."""

    try:
        await mode_action(
            terminal,
            pids_data,
            args.action,
            yes=getattr(args, "yes", False),
            dry_run=getattr(args, "dry_run", False),
            as_json=args.json,
            verbose=args.verbose,
            save=getattr(args, "save", False),
            label=getattr(args, "label", None),
            vehicle_states=getattr(args, "state", None),
            notes=getattr(args, "notes", None),
            profile=active(),
        )
    except ActionError as e:
        messages.fail(str(e), code=2)


async def handle_reset(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    """``canair reset ECU TYPE``: one ECUReset, after the rule and the user agree."""
    profile = active()
    tx_id = resolve_tx(args.reset, profile=profile)
    if tx_id is None:
        messages.fail(
            f"could not resolve ECU '{args.reset}' (use a name like BMS or a hex TX id like 7E4)",
            code=2,
        )
    reset_type = RESET_TYPES[args.reset_type]
    # Before the question: a refused reset is never asked about.
    blocked = reset_block(reset_type, terminal.session_policy.protocol(tx_id), tx_id)
    if blocked is not None:
        raise SafetyBlocked(blocked)
    name = ecu_name(tx_id, profile=profile)
    word = f"{tx_id:03X}" if name.startswith("0x") else name
    ecu = ecu_display(tx_id, profile=profile)
    messages.warning(
        f"{ecu} stops working while it restarts. Reset it only with the car parked and not in use"
    )
    question = f"Reset {ecu} ({reset_type_name(reset_type)}, 11 {reset_type:02X})?"
    if not typed_confirm(question, word, yes=getattr(args, "yes", False)):
        raise SystemExit(1)
    accepted = await mode_ecu_reset(
        terminal,
        tx_id,
        reset_type,
        ecu=ecu,
        session=args.session,
        wake=args.wake,
        wake_plan=wake_plan(tx_id, profile=profile, wake=args.wake, pids_data=pids_data),
        restart_timeout=args.restart_timeout,
        as_json=args.json,
        verbose=args.verbose,
    )
    if not accepted:
        raise SystemExit(1)
