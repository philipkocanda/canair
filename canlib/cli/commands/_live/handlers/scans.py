"""Handlers for the sweeps: PID/DID scans, routine and IOControl probes, discovery.

Three of these pick their UDS or KWP2000 variant per ECU via
:func:`canlib.ecus.split_ecus_by_protocol` — a safety boundary, not a convenience:
UDS RoutineControl (0x31) means StartRoutineByLocalIdentifier on a KWP2000 ECU and
can actuate, so the read-only 0x33/0x30 variants must be used there instead.
"""

from __future__ import annotations

import sys

from canlib.cli.commands._live.operation import LiveOperation
from canlib.ecus import split_ecus_by_protocol
from canlib.live.discover import mode_discover
from canlib.live.iocontrol_scan import mode_iocontrol_scan
from canlib.live.routines_scan import mode_routines_scan
from canlib.live.scan import mode_scan
from canlib.transport.protocol import Terminal

from .ranges import parse_range, parse_ranges


async def handle_scan(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    from canlib.ecus import resolve_tx

    tx_id = resolve_tx(args.tx)
    if tx_id is None:
        print(
            f"Error: could not resolve ECU '{args.tx}' "
            "(use a name like BMS or a hex TX id like 7E4)",
            file=sys.stderr,
        )
        sys.exit(1)
    service = int(args.service, 16) if args.service else 0x21
    pid_ranges = parse_ranges(args.range) if args.range else ((0x01, 0xFF),)
    append_bytes = ""
    if args.append:
        cleaned = args.append.replace(" ", "").upper()
        if not all(c in "0123456789ABCDEF" for c in cleaned) or len(cleaned) % 2 != 0:
            print(
                "Error: --append must be valid hex bytes (e.g., 03 or 030A0A05)",
                file=sys.stderr,
            )
            sys.exit(1)
        append_bytes = cleaned
    await mode_scan(
        terminal,
        tx_id,
        service,
        pid_ranges,
        args.verbose,
        args.json,
        append_bytes=append_bytes,
        session=args.session,
        wake=args.wake,
        save=args.save,
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
    )


async def handle_routines_scan(
    args, terminal: Terminal, pids_data, host, *, reconnect=None
) -> None:
    from canlib.live.kwp_routines_scan import (
        UNPROVEN_WARNING,
        mode_kwp_routines_scan,
        needs_confirmation,
    )

    rid_range = parse_range(args.rid_range)

    # Auto-select by id_protocol. UDS ECUs use RoutineControl (0x31 SF03,
    # requestRoutineResults). KWP2000 ECUs (BMS/VCU/MCU/LDC/AAF) MUST NOT
    # receive 0x31 — there it means StartRoutineByLocalIdentifier (actuates);
    # they use the read-only 0x33 RequestRoutineResultsByLocalIdentifier.
    uds_ecus, kwp_ecus = split_ecus_by_protocol(args.routines_scan)

    _session = getattr(args, "session", False)
    _wake = getattr(args, "wake", False)
    _mode = getattr(args, "session_mode", "03")

    # Asked before anything is sent, like a DTC clear. A closed/non-interactive
    # stdin reads as "no", so a script must pass --yes explicitly.
    confirmed = bool(getattr(args, "yes", False))
    if kwp_ecus and needs_confirmation(pids_data) and not confirmed:
        print(f"!! WARNING: {UNPROVEN_WARNING}", file=sys.stderr)
        print(
            f"!! Sweep {', '.join(kwp_ecus)} with 0x33 anyway? [y/N] ",
            end="",
            flush=True,
            file=sys.stderr,
        )
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return
        confirmed = True

    if uds_ecus:
        await mode_routines_scan(
            terminal,
            pids_data,
            ecus=uds_ecus,
            rid_range=rid_range,
            throttle_ms=args.throttle_ms,
            verbose=args.verbose,
            write_yaml=True,
            session=_session,
            wake=_wake,
            session_mode=_mode,
        )
    if kwp_ecus:
        # For KWP2000 ECUs the id is an 8-bit LID; only pass an explicit range
        # if the user gave one that fits a single byte, else use the 00-FF default.
        lid_range = rid_range if rid_range[1] <= 0xFF else None
        await mode_kwp_routines_scan(
            terminal,
            pids_data,
            ecus=kwp_ecus,
            lid_range=lid_range,
            throttle_ms=args.throttle_ms,
            verbose=args.verbose,
            write_yaml=True,
            session=_session,
            wake=_wake,
            session_mode=_mode,
            confirmed=confirmed,
        )


async def handle_iocontrol_scan(
    args, terminal: Terminal, pids_data, host, *, reconnect=None
) -> None:
    from canlib.live.kwp_iocontrol_scan import (
        UNPROVEN_WARNING,
        mode_kwp_iocontrol_scan,
        needs_confirmation,
    )

    did_range = parse_range(args.did_range) if args.did_range else None

    # Auto-select the service by the ECU's identity protocol: KWP2000 ECUs
    # (BMS/VCU/MCU/LDC/AAF) use IOControlByLocalIdentifier (0x30); the rest
    # use UDS IOControlByIdentifier (0x2F).
    uds_ecus, kwp_ecus = split_ecus_by_protocol(args.iocontrol_scan)

    _session = getattr(args, "session", False)
    _wake = getattr(args, "wake", False)
    # None = not given: the KWP2000 scanner then picks the profile's session.
    _mode = getattr(args, "session_mode", None)

    # Asked before anything is sent, like the routine scan. A closed or
    # non-interactive stdin reads as "no", so a script must pass --yes.
    confirmed = bool(getattr(args, "yes", False))
    if kwp_ecus and needs_confirmation(pids_data) and not confirmed:
        print(f"!! WARNING: {UNPROVEN_WARNING}", file=sys.stderr)
        print(
            f"!! Sweep {', '.join(kwp_ecus)} with 30 {{LID}} 00 anyway? [y/N] ",
            end="",
            flush=True,
            file=sys.stderr,
        )
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return
        confirmed = True

    if uds_ecus:
        await mode_iocontrol_scan(
            terminal,
            pids_data,
            ecus=uds_ecus,
            did_range=did_range,
            throttle_ms=args.throttle_ms,
            verbose=args.verbose,
            write_yaml=True,
            session=_session,
            wake=_wake,
            session_mode=_mode or "03",
        )
    if kwp_ecus:
        await mode_kwp_iocontrol_scan(
            terminal,
            pids_data,
            ecus=kwp_ecus,
            lid_range=did_range,
            throttle_ms=args.throttle_ms,
            verbose=args.verbose,
            write_yaml=True,
            session=_session,
            wake=_wake,
            session_mode=_mode,
            confirmed=confirmed,
        )


async def handle_sessions_scan(
    args, terminal: Terminal, pids_data, host, *, reconnect=None
) -> None:
    from canlib.live.sessions_scan import mode_sessions_scan

    modes = None
    if getattr(args, "modes", None):
        modes = tuple(int(tok, 16) for tok in str(args.modes).replace(" ", "").split(",") if tok)
    await mode_sessions_scan(
        terminal,
        pids_data,
        ecus=args.sessions_scan,
        modes=modes,
        throttle_ms=args.throttle_ms,
        verbose=args.verbose,
        write_yaml=True,
    )


async def handle_discover(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    from canlib.live.discover import DEFAULT_TESTER_ADDRESS
    from canlib.uds.addressing import is_extended, resolve_mode

    if getattr(args, "functional", False):
        from canlib.live.discover_functional import DEFAULT_REQUEST, mode_discover_functional
        from canlib.transport.functional import DEFAULT_WINDOW_S

        # Set only by the discover parser, whose --request/--window default to None
        # (meaning the defaults here) and whose functional_args_error has already
        # refused a non-positive --window, so a None test cannot hide a zero.
        await mode_discover_functional(
            terminal,
            pids_data,
            request=DEFAULT_REQUEST if args.request is None else args.request,
            window=DEFAULT_WINDOW_S if args.window is None else args.window,
            repeat=args.repeat,
            interval=args.interval,
            as_json=args.json,
            save=args.save,
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
            register=getattr(args, "register", False),
            dry_run=getattr(args, "dry_run", False),
            identify=getattr(args, "identify", False),
        )
        return

    _addr_mode = resolve_mode(pids_data)
    # 11-bit sweeps arbitration ids (default 0x700-0x7EF); 29-bit sweeps the
    # target-address byte (default 0x00-0xFF), formed into 0x18DA{target}{tester}.
    if args.range is not None:
        addr_range = parse_range(args.range)
    elif is_extended(_addr_mode):
        addr_range = (0x00, 0xFF)
    else:
        addr_range = (0x700, 0x7EF)
    await mode_discover(
        terminal,
        addr_range,
        args.verbose,
        args.json,
        delay=args.delay,
        save=args.save,
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
        register=getattr(args, "register", False),
        dry_run=getattr(args, "dry_run", False),
        identify=getattr(args, "identify", False),
        mode=_addr_mode,
        tester=DEFAULT_TESTER_ADDRESS,
    )


# What the sweeping subcommands run: ``scan`` and ``discover`` record captures, and a scan's
# aborted previous run is announced by its type.
RANGE = LiveOperation(handle_scan, records=True, scan_types=frozenset({"scan"}))
IOCONTROL = LiveOperation(handle_iocontrol_scan, scan_types=frozenset({"iocontrol"}))
ROUTINES = LiveOperation(handle_routines_scan, scan_types=frozenset({"routines"}))
SESSIONS = LiveOperation(handle_sessions_scan)
DISCOVER = LiveOperation(handle_discover, records=True)
