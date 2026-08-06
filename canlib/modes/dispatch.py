"""The shared live-command dispatcher: one subcommand -> one mode handler.

Every live subcommand populates the same argument namespace and then arrives here,
where the selected mode is invoked over a :class:`~canlib.transport.protocol.Terminal`.
Typed against that protocol rather than a concrete class, so the same dispatch
serves both transports (ELM327 over WebSocket/TCP, and raw SLCAN with client-side
ISO-TP) and a mode reaching for a transport-specific attribute is a ``ty`` error —
the compiler-checked form of the "keep the WiCAN replaceable" rule.

It lives in :mod:`canlib.modes` rather than beside the CLI because it dispatches
*to* this package and is called *from* it: :mod:`canlib.modes.raw_ops` runs the
raw transport through :func:`run_session_guarded`, which previously meant a mode
importing upward into ``canlib.commands._live``. The CLI keeps only what is
genuinely CLI: building the argument namespace, and constructing the terminal.
"""

from __future__ import annotations

import argparse
import re
import sys

from canlib.ecus import split_ecus_by_protocol
from canlib.keepmode import keep_mode_from_args, wants_save
from canlib.modes import (
    mode_discover,
    mode_ecu,
    mode_identity,
    mode_interactive,
    mode_monitor,
    mode_multi,
    mode_param,
    mode_raw,
    mode_scan,
    mode_skm_wakeup,
)
from canlib.modes.iocontrol import mode_iocontrol_execute
from canlib.modes.iocontrol_scan import mode_iocontrol_scan
from canlib.modes.routines import mode_routines_execute
from canlib.modes.routines_scan import mode_routines_scan
from canlib.notation import resolve_notation
from canlib.states import parse_states
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.transport.protocol import Terminal


def parse_range(range_str: str) -> tuple[int, int]:
    """Parse a PID/DID range like '01-FF', 'E000-E0FF', or 'BC01-BC0B'."""
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid range: {range_str}. Expected format: 01-FF or E000-E0FF"
        )
    return int(match.group(1), 16), int(match.group(2), 16)


async def run_session_guarded(
    args, terminal: Terminal, pids_data, host, *, transport_label: str, reconnect=None
) -> int:
    """Run :func:`dispatch_mode` (+ optional timings) under unified error handling.

    The single, transport-agnostic home for "a live session hit a transport/IO
    failure" — shared by both the ELM (``async_main``) and raw (``run_raw``)
    entry points so a dropped/failed bus is always a clean, classified message
    (never a traceback), with a ``--recover`` hint when a ``--save`` session was
    in flight (its data is safe in the write-ahead journal). Returns a process
    exit code: 0 on success, 1 on a transport failure.

    The caller owns the terminal lifecycle (construct/close) and any
    transport-specific ``finally`` (session-end logging, reboot). ``KeyboardInterrupt``
    is intentionally *not* caught here — the mode handlers reconcile their journals
    on interrupt, and ``run_live`` reports the interrupt.
    """
    from canlib.transport.errors import describe_transport_error, transport_error_types

    try:
        await dispatch_mode(args, terminal, pids_data, host, reconnect=reconnect)
        if getattr(args, "timings", False):
            from canlib.timing import print_timings

            print_timings(terminal.timings, as_json=getattr(args, "json", False))
        return 0
    except transport_error_types() as e:
        print(
            "error: "
            + describe_transport_error(
                e, host=host, transport_label=transport_label, saving=wants_save(args)
            ),
            file=sys.stderr,
        )
        return 1


async def dispatch_mode(args, terminal: Terminal, pids_data, host, *, reconnect=None):
    """Dispatch a live subcommand to its mode handler over ``terminal``.

    Shared by the ELM (WiCANTerminal) and raw (RawTerminal) transports so the
    same commands work on either — the transport differs, the dispatch does not.
    Typed against the :class:`~canlib.transport.protocol.Terminal` contract, not
    a concrete class, so a mode reaching for a transport-specific attribute is a
    ``ty`` error (the "keep the WiCAN replaceable" rule, compiler-checked).

    ``reconnect`` is the caller's mid-session re-home strategy for the monitor, and
    is **injected** rather than built here: each transport has its own (the CLI
    passes ``build_elm_reconnector``; the raw path uses ``build_raw_reconnector``
    and never reaches this branch, since ``raw_ops`` routes monitoring to its
    pipelined client first). Constructing the ELM one inside a
    transport-agnostic dispatcher both contradicted that claim and was the last
    thing tying this module to the command layer.
    """
    if args.multi and args.monitor:
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
    elif args.multi:
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
    elif args.skm_wakeup:
        # The SKM relay-wake is a make-specific Ioniq capability (relay DIDs /
        # magic bytes / addresses are Ioniq-particular), gated behind the profile
        # `skm_wakeup` quirk. Reading a fast-sleeping ECU is the make-neutral
        # per-ECU `wake:` block (canlib.wake) — not this command.
        from canlib.quirks import SKM_WAKEUP, has_quirk

        if not has_quirk(pids_data, SKM_WAKEUP):
            prof_name = getattr(getattr(args, "_profile", None), "name", None) or "this profile"
            print(
                f"Error: skm-wake is not supported by {prof_name} — it requires the "
                "`skm_wakeup` capability (declare it under `quirks:` in profile.yaml). "
                "To merely wake a fast-sleeping ECU, declare a per-ECU `wake:` block "
                "(`canair pids set-wake`) and use `session <ECU> --wake`.",
                file=sys.stderr,
            )
            sys.exit(1)
        # skm-wake relies on ELM327 text semantics (raw ATSH/frame collection)
        # provided by the shared Elm327Terminal engine — not the raw slcan-tcp
        # RawTerminal. Guard on the engine type so raw reports a clear error.
        if not isinstance(terminal, Elm327Terminal):
            print(
                "Error: skm-wake is only supported on the ELM327 transports "
                "(wican-ws / elm327-tcp), not slcan-tcp.",
                file=sys.stderr,
            )
            sys.exit(1)
        await mode_skm_wakeup(terminal, args.level, args.verbose)
    elif args.identity:
        from canlib.ecus import resolve_tx

        tx_id = resolve_tx(args.tx)
        if tx_id is None:
            print(
                f"Error: could not resolve ECU '{args.tx}' "
                "(use a name like IGPM or a hex TX id like 770)",
                file=sys.stderr,
            )
            sys.exit(1)
        from canlib.quirks import resolve_quirks

        await mode_identity(
            terminal,
            tx_id,
            session=args.session,
            wake=args.wake,
            as_json=args.json,
            protocol=getattr(args, "protocol", "auto"),
            quirks=resolve_quirks(pids_data),
        )
    elif args.dtc or getattr(args, "dtc_all", False):
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
                print(
                    f"Error: --group must be hex (e.g. FFFFFF), got {args.group!r}", file=sys.stderr
                )
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
    elif args.param:
        await mode_param(
            terminal,
            pids_data,
            args.param,
            args.verbose,
            args.json,
            session=args.session,
            wake=args.wake,
        )
    elif args.ecu:
        await mode_ecu(
            terminal,
            pids_data,
            args.ecu,
            args.pid,
            args.verbose,
            args.json,
            session=args.session,
            wake=args.wake,
        )
    elif args.raw:
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
        )
    elif args.scan:
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
    elif args.iocontrol:
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
    elif args.routines:
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
    elif args.routines_scan is not None:
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
    elif args.iocontrol_scan is not None:
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
    elif getattr(args, "sessions_scan", None) is not None:
        from canlib.modes.sessions_scan import mode_sessions_scan

        modes = None
        if getattr(args, "modes", None):
            modes = tuple(
                int(tok, 16) for tok in str(args.modes).replace(" ", "").split(",") if tok
            )
        await mode_sessions_scan(
            terminal,
            pids_data,
            ecus=args.sessions_scan,
            modes=modes,
            throttle_ms=args.throttle_ms,
            verbose=args.verbose,
            write_yaml=True,
        )
    elif args.discover:
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
    else:
        # The interactive REPL is an ELM327 command prompt: it sets the ECU
        # header via raw ``ATSH`` sends, which are no-ops on the raw transport
        # (RawTerminal uses set_header(), not ATSH parsing), so a UDS request
        # would fire with no header set. Guard on the ELM327 engine type so raw
        # reports a clear error instead of a half-working prompt.
        if not isinstance(terminal, Elm327Terminal):
            print(
                "Error: the interactive REPL is only supported on the ELM327 "
                "transports (wican-ws / elm327-tcp), not slcan-tcp.",
                file=sys.stderr,
            )
            sys.exit(1)
        await mode_interactive(terminal, pids_data, args.verbose)
