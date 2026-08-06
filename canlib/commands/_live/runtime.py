"""The live-command runtime: acquire the device, run a session, release it.

``run_live`` owns the process-level concerns (the connection mutex, the signal
handling that makes SIGTERM/SIGHUP unwind like Ctrl-C, the lock watchdog);
``async_main`` owns one session's lifecycle (transport selection and fallback,
pre-flight checks, terminal construction, and the ``finally`` that closes and
reboots). The mode dispatch itself is not here — it lives in
:mod:`canlib.modes.dispatch`, which this calls into.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys

from canlib import (
    init_logging,
    load_pids,
    log_command,
    reboot_wican,
)
from canlib.keepmode import wants_save
from canlib.lock import WiCANLock
from canlib.lock_watchdog import LockWatchdog
from canlib.modes.dispatch import run_session_guarded
from canlib.modes.iocontrol import mode_iocontrol_list
from canlib.modes.routines import mode_routines_list
from canlib.stop_signals import graceful_stop
from canlib.transport.protocol import Terminal

from .connect import _print_sleep_banner, build_elm_reconnector, connect_elm_terminal


async def async_main(args):
    """Main async entry point."""
    from canlib.config import fallback_settings
    from canlib.transport import (
        resolve_transport_candidates,
        select_reachable_transport,
        wait_for_reachable,
    )

    candidates = resolve_transport_candidates(args)
    _, _connect_timeout, _ = fallback_settings()
    if getattr(args, "wait", False):
        # --wait: block until a candidate comes online (Ctrl-C to stop). No custom
        # signal handler is installed yet, so a SIGINT here raises KeyboardInterrupt
        # which run_live reports cleanly.
        transport = (
            wait_for_reachable(
                candidates,
                connect_timeout=_connect_timeout,
                deadline=None,
                notice=lambda m: print(f"note: {m}", file=sys.stderr),
            )
            or candidates[0]
        )
    else:
        transport = select_reachable_transport(candidates, connect_timeout=_connect_timeout)
    host = transport.host

    init_logging()
    log_command(
        f"--- SESSION START (host={host}, mode={'interactive' if not any([args.param, args.ecu, args.raw, args.scan, args.discover, args.skm_wakeup, args.identity, args.dtc, args.iocontrol, args.routines, args.routines_scan is not None, args.iocontrol_scan is not None, getattr(args, 'sessions_scan', None) is not None]) else 'batch'}, unsafe={args.unsafe}, session={getattr(args, 'session', False)}) ---"
    )

    if args.unsafe:
        print("!! WARNING: --unsafe mode active. Dangerous command blocklist is bypassed.")
        print("!! Each blocked command will require explicit user consent before execution.")
        print()

    pids_data = load_pids()

    # Raw transport (slcan-tcp): route to the client-side ISO-TP/UDS path instead
    # of the ELM327 WebSocket. The device must already be in slcan mode.
    if transport.is_raw:
        from canlib.modes.raw_ops import run_raw

        return await run_raw(args, transport, pids_data)

    # Past the raw early-return we're on the WebSocket (WiCANTerminal) path,
    # which always resolves a host (via --wican/config/default_wican).
    assert host is not None

    # Warn about any aborted scans from a previous interrupted session — but
    # only in the scan subcommand that produces that scan type, so unrelated
    # commands (query, io, identity, ...) stay quiet.
    _scan_types: set[str] = set()
    if args.scan:
        _scan_types.add("scan")
    if args.iocontrol_scan is not None:
        _scan_types.add("iocontrol")
    if args.routines_scan is not None:
        _scan_types.add("routines")

    if _scan_types:
        from canlib.scan_state import find_aborted_scans

        _aborted = [s for s in find_aborted_scans() if s.get("type") in _scan_types]
        if _aborted:
            print("!! Aborted scan(s) detected from a previous session:")
            for _s in _aborted:
                print(
                    f"   [{_s['type'].upper()} scan  {_s['ecu']} @ {_s['tx_id']}]"
                    f"  range {_s['range']}"
                    f"  last probe: {_s['current']}"
                    f"  ({_s['hits']} hits / {_s['total']} total)"
                    f"  started {_s.get('started', '?')}"
                )
            print("!! To resume, re-run with the same ECU and range starting at the last probe.")
            print()

    # List-only mode: no CAN connection needed (--json or explicit list)
    if args.iocontrol and not args.did and args.json:
        mode_iocontrol_list(pids_data, args.iocontrol, as_json=True)
        return
    if args.routines and not args.rid and args.json:
        mode_routines_list(pids_data, args.routines, as_json=True)
        return

    # ELM327 init string from the active profile. Required (validate enforces
    # it); a profile missing it fails loud rather than silently using a stale
    # Ioniq-flavoured fallback.
    init_string = pids_data.get("init")
    if not init_string:
        print(
            "error: the active profile has no `init:` string (profile.yaml). "
            'Add one, e.g. `init: "ATSP6;ATS0;ATAL;"` (ISO 15765-4 11-bit).',
            file=sys.stderr,
        )
        sys.exit(1)

    # Fail loud: --save (and metadata flags) only apply to capture-producing modes.
    _wants_save = wants_save(args)
    if _wants_save:
        _save_ok = bool(args.scan or args.raw or args.discover)
        if not _save_ok and args.multi:
            from canlib.modes.multi import parse_sub_commands

            _save_ok = any(c["type"] in ("query", "raw") for c in parse_sub_commands(args.multi))
        if not _save_ok:
            print(
                "Error: --save/--label/--state/--notes only apply to --scan, --raw, "
                "--discover, or --multi with a 'query'/'raw' step.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Surface any orphaned journals from a previously killed --save session.
        from canlib.commands.captures import orphan_notice

        orphan_notice()

    # Pre-flight reachability check so a silent host fails fast with guidance
    # instead of hanging out a connect timeout deep in the stack. For a WiCAN the
    # ELM327 WebSocket lives on the HTTP port (require_ws_reachable); a direct
    # ELM327 adapter (elm327-tcp) has no HTTP API, so we probe its data port.
    from canlib.wican_mode import ModeError, require_elm327_tcp_reachable, require_ws_reachable

    is_elm_tcp = transport.type == "elm327-tcp"
    transport_label = "ELM327/TCP" if is_elm_tcp else "WebSocket"
    try:
        if is_elm_tcp:
            from canlib.transport import DEFAULT_ELM327_TCP_PORT

            require_elm327_tcp_reachable(host, transport.port or DEFAULT_ELM327_TCP_PORT)
        else:
            require_ws_reachable(host)
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    from canlib.transport.errors import describe_transport_error, transport_error_types

    rc = 0
    terminal: Terminal | None = None
    try:
        try:
            terminal = await connect_elm_terminal(transport, pids_data, args)
            if transport.is_wican_http:
                _print_sleep_banner(host)

            if args.wake:
                args.session = True
        except transport_error_types() as e:
            # Connection setup failed (connect / ELM init). Classify + report
            # cleanly rather than dumping a traceback; no session ran yet.
            print(
                "error: "
                + describe_transport_error(
                    e, host=host, transport_label=transport_label, saving=_wants_save
                ),
                file=sys.stderr,
            )
            return 1

        # Session dispatch under the shared, transport-agnostic error guard.
        rc = await run_session_guarded(
            args,
            terminal,
            pids_data,
            host,
            transport_label=transport_label,
            reconnect=build_elm_reconnector(args, pids_data),
        )
    finally:
        if terminal is not None:
            await terminal.close()
        log_command("--- SESSION END ---")

        if args.reboot and transport.is_wican_http:
            reboot_wican(host)
    return rc


def run_live(args) -> int:
    """Acquire the device lock and run ``async_main`` for a live subcommand."""
    lock = WiCANLock()
    lock.acquire(force=args.force)

    # Map SIGTERM/SIGHUP onto the same graceful path as Ctrl-C (SIGINT already
    # raises KeyboardInterrupt). So a `kill`/`pkill -f canair`, a vanished
    # terminal (SSH drop / closed window), or the lock watchdog's self-abort all
    # unwind cleanly (closes the terminal, reconciles any --save journal,
    # releases the device connection) instead of terminating abruptly. Modes that
    # install their own handling (the monitor) override this for their duration.
    def _on_stop(_sig, _frame):
        raise KeyboardInterrupt

    # Stand down if another run asks for the connection with --force: an orphaned
    # session (terminal gone, no hangup ever delivered) otherwise holds the
    # device's single connection indefinitely, and canair never kills it for you.
    # Watching is scoped *inside* the handler installation, so the watchdog's
    # self-SIGTERM can never land after the graceful handler was restored.
    watchdog = LockWatchdog(lock)
    try:
        with graceful_stop(_on_stop):
            watchdog.start()
            try:
                result = asyncio.run(async_main(args))
            finally:
                watchdog.stop()
    except KeyboardInterrupt:
        with contextlib.suppress(OSError):
            # The terminal may be gone (SIGHUP) — a failed write must not mask
            # the clean shutdown that already happened.
            print("\nInterrupted.")
        return 0
    finally:
        lock.release()
    return result if isinstance(result, int) else 0


def run(args) -> int:
    """Default live dispatch used by most subcommands (set via finalize_live_parser)."""
    return run_live(args)
