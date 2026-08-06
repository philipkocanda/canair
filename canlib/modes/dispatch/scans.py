"""Dispatch for the sweeps: PID/DID scans, routine and IOControl probes, discovery.

Three of these pick their UDS or KWP2000 variant per ECU via
:func:`canlib.ecus.split_ecus_by_protocol` — a safety boundary, not a convenience:
UDS RoutineControl (0x31) means StartRoutineByLocalIdentifier on a KWP2000 ECU and
can actuate, so the read-only 0x33/0x30 variants must be used there instead.
"""

from __future__ import annotations

import sys

from canlib.ecus import split_ecus_by_protocol
from canlib.modes import mode_discover, mode_scan
from canlib.modes.iocontrol_scan import mode_iocontrol_scan
from canlib.modes.routines_scan import mode_routines_scan
from canlib.transport.protocol import Terminal

from .ranges import parse_range


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
    pid_range = parse_range(args.range) if args.range else (0x01, 0xFF)
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
        pid_range,
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
    from canlib.modes.kwp_routines_scan import mode_kwp_routines_scan

    rid_range = parse_range(args.rid_range)

    # Auto-select by id_protocol. UDS ECUs use RoutineControl (0x31 SF03,
    # requestRoutineResults). KWP2000 ECUs (BMS/VCU/MCU/LDC/AAF) MUST NOT
    # receive 0x31 — there it means StartRoutineByLocalIdentifier (actuates);
    # they use the read-only 0x33 RequestRoutineResultsByLocalIdentifier.
    uds_ecus, kwp_ecus = split_ecus_by_protocol(args.routines_scan)

    _session = getattr(args, "session", False)
    _wake = getattr(args, "wake", False)
    _mode = getattr(args, "session_mode", "03")

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
        )


async def handle_iocontrol_scan(
    args, terminal: Terminal, pids_data, host, *, reconnect=None
) -> None:
    from canlib.modes.kwp_iocontrol_scan import mode_kwp_iocontrol_scan

    did_range = parse_range(args.did_range) if args.did_range else None

    # Auto-select the service by the ECU's identity protocol: KWP2000 ECUs
    # (BMS/VCU/MCU/LDC/AAF) use IOControlByLocalIdentifier (0x30); the rest
    # use UDS IOControlByIdentifier (0x2F).
    uds_ecus, kwp_ecus = split_ecus_by_protocol(args.iocontrol_scan)

    _session = getattr(args, "session", False)
    _wake = getattr(args, "wake", False)
    _mode = getattr(args, "session_mode", "03")

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
            session_mode=_mode,
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
        )


async def handle_sessions_scan(
    args, terminal: Terminal, pids_data, host, *, reconnect=None
) -> None:
    from canlib.modes.sessions_scan import mode_sessions_scan

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
    from canlib.addressing import is_extended, resolve_mode
    from canlib.modes.discover import DEFAULT_TESTER_ADDRESS

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
