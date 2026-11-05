"""The live-command runtime: acquire the device, run a session, release it.

``run_live`` owns the process-level concerns (the connection mutex, the signal
handling that makes SIGTERM/SIGHUP unwind like Ctrl-C, the lock watchdog);
``async_main`` owns one session's lifecycle (transport selection and fallback,
pre-flight checks, terminal construction, and the ``finally`` that closes and
reboots). What the session does is the command's :class:`LiveOperation`; the session
itself (error classification, revision detection, frame-count write-back) is
:mod:`canair.live.session`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from canair.captures.keepmode import wants_save
from canair.cli.args.transport import transport_overrides
from canair.core.stop_signals import graceful_stop
from canair.device.lock import WiCANLock
from canair.device.lock_watchdog import LockWatchdog
from canair.ecus.pids import load_pids
from canair.live.session import SessionOptions, run_session_guarded
from canair.render import messages
from canair.transport.config import ConnectionOptions
from canair.transport.ecu_log import init_logging
from canair.transport.protocol import Terminal
from canair.transport.reboot import reboot_after_session

from .connect import _print_sleep_banner, build_elm_reconnector, connect_elm_terminal
from .operation import LiveOperation


def connection_options(args: argparse.Namespace) -> ConnectionOptions:
    """The connection flags on ``args``."""
    return ConnectionOptions(
        verbose=bool(getattr(args, "verbose", False)),
        unsafe=bool(getattr(args, "unsafe", False)),
        timeout=getattr(args, "timeout", None),
        elm_timeout=getattr(args, "elm_timeout", None),
        wait=bool(getattr(args, "wait", False)),
        reboot=bool(getattr(args, "reboot", False)),
    )


def saving(args: argparse.Namespace) -> bool:
    """Whether the run intends to record captures (``--save`` or a metadata flag)."""
    return wants_save(
        save=bool(getattr(args, "save", False)),
        label=getattr(args, "label", None),
        state=getattr(args, "state", None),
        notes=getattr(args, "notes", None),
    )


def session_options(args: argparse.Namespace) -> SessionOptions:
    """The session flags on ``args``."""
    steps = getattr(args, "multi", None)
    return SessionOptions(
        as_json=bool(getattr(args, "json", False)),
        timings=bool(getattr(args, "timings", False)),
        verbose=bool(getattr(args, "verbose", False)),
        learn_frames=not getattr(args, "no_learn_frames", False),
        saving=saving(args),
        steps=tuple(steps) if steps else None,
    )


async def async_main(args, operation: LiveOperation):
    """Main async entry point."""
    from canair.config import fallback_settings
    from canair.transport import (
        resolve_transport_candidates,
        select_reachable_transport,
        wait_for_reachable,
    )

    conn = connection_options(args)
    candidates = resolve_transport_candidates(transport_overrides(args))
    _, _connect_timeout, _ = fallback_settings()
    if conn.wait:
        # --wait: block until a candidate comes online (Ctrl-C to stop). No custom
        # signal handler is installed yet, so a SIGINT here raises KeyboardInterrupt
        # which run_live reports cleanly.
        transport = (
            wait_for_reachable(
                candidates,
                connect_timeout=_connect_timeout,
                deadline=None,
                notice=messages.note,
            )
            or candidates[0]
        )
    else:
        transport = select_reachable_transport(candidates, connect_timeout=_connect_timeout)
    host = transport.host

    init_logging()

    if conn.unsafe:
        messages.warning(
            "--unsafe mode active. The dangerous-command blocklist is bypassed.\n"
            "Each blocked command will require explicit consent before it is sent."
        )

    pids_data = load_pids()

    # Raw transport (slcan-tcp): route to the client-side ISO-TP/UDS path instead
    # of the ELM327 WebSocket. The device must already be in slcan mode.
    if transport.is_raw:
        from canair.live.read.raw_ops import run_raw

        async def operate(terminal: Terminal, pids_data: dict) -> None:
            await operation.handler(args, terminal, pids_data, host)

        return await run_raw(
            transport,
            pids_data,
            conn=conn,
            session=session_options(args),
            operation=operate,
            monitor=operation.monitor,
            candidates=candidates,
        )

    # Past the raw early-return we're on the WebSocket (WiCANTerminal) path,
    # which always resolves a host (via --wican/config/default_wican).
    assert host is not None

    # Warn about any aborted scans from a previous interrupted session — but
    # only in the scan subcommand that produces that scan type, so unrelated
    # commands (query, io, identity, ...) stay quiet.
    if operation.scan_types:
        from canair.live.scan.progress import find_aborted_scans

        _aborted = [s for s in find_aborted_scans() if s.get("type") in operation.scan_types]
        if _aborted:
            lines = ["Aborted scan(s) detected from a previous session:"]
            for _s in _aborted:
                lines.append(
                    f" [{_s['type'].upper()} scan  {_s['ecu']} @ {_s['tx_id']}]"
                    f"  range {_s['range']}"
                    f"  last probe: {_s['current']}"
                    f"  ({_s['hits']} hits / {_s['total']} total)"
                    f"  started {_s.get('started', '?')}"
                )
            messages.warning("\n".join(lines))
            messages.hint(
                "to resume, re-run with the same ECU and range starting at the last probe"
            )

    # ELM327 init string from the active profile. Required (validate enforces
    # it); a profile missing it fails loud rather than silently using a stale
    # Ioniq-flavoured fallback.
    init_string = pids_data.get("init")
    if not init_string:
        messages.fail(
            "the active profile has no `init:` string (profile.yaml). "
            'Add one, e.g. `init: "ATSP6;ATS0;ATAL;"` (ISO 15765-4 11-bit).'
        )

    # Fail loud: --save (and metadata flags) only apply to capture-producing modes.
    _wants_save = saving(args)
    if _wants_save:
        if not operation.records:
            messages.fail(
                "--save/--label/--state/--notes only apply to a scan, raw, discover or "
                "dtc run, or to read/monitor with a 'query'/'raw' step.",
                code=2,
            )
        # Surface any orphaned journals from a previously killed --save session.
        from canair.cli.commands.captures import orphan_notice

        orphan_notice()

    # Pre-flight reachability check so a silent host fails fast with guidance
    # instead of hanging out a connect timeout deep in the stack. For a WiCAN the
    # ELM327 WebSocket lives on the HTTP port (require_ws_reachable); a direct
    # ELM327 adapter (elm327-tcp) has no HTTP API, so we probe its data port.
    # This cheap TCP check runs before the firmware probe, so an offline host
    # fails after one connect timeout rather than after the probe's too.
    from canair.device.wican.mode import (
        ModeError,
        require_elm327_tcp_reachable,
        require_ws_reachable,
    )

    is_elm_tcp = transport.type == "elm327-tcp"
    transport_label = "ELM327/TCP" if is_elm_tcp else "WebSocket"

    try:
        if is_elm_tcp:
            from canair.transport import DEFAULT_ELM327_TCP_PORT

            require_elm327_tcp_reachable(host, transport.port or DEFAULT_ELM327_TCP_PORT)
        else:
            require_ws_reachable(host)
    except ModeError as e:
        messages.fail(str(e))

    # Firmware v6 has no /ws terminal: refuse wican-ws with the working
    # alternative rather than letting the upgrade fail with a bare 404. An
    # elm327-tcp host is probed only with `firmware_check: always` (it is
    # usually a clone with no HTTP server), and then only ever adds a note.
    from canair.device.wican.firmware import check, print_verdict

    verdict = check(host, transport.type)
    print_verdict(verdict)
    if verdict.refused:
        return 2

    from canair.transport.errors import describe_transport_error, transport_error_types

    rc = 0
    terminal: Terminal | None = None
    try:
        try:
            terminal = await connect_elm_terminal(transport, pids_data, conn)
            if transport.is_wican_http:
                _print_sleep_banner(host)

            if getattr(args, "wake", False):
                args.session = True
        except transport_error_types() as e:
            # Connection setup failed (connect / ELM init). Classify + report
            # cleanly rather than dumping a traceback; no session ran yet.
            messages.error(
                describe_transport_error(
                    e, host=host, transport_label=transport_label, saving=_wants_save
                )
            )
            return 1

        # Session dispatch under the shared, transport-agnostic error guard.
        reconnect = build_elm_reconnector(candidates, pids_data, conn)

        async def operate(terminal: Terminal, pids_data: dict) -> None:
            await operation.handler(args, terminal, pids_data, host, reconnect=reconnect)

        rc = await run_session_guarded(
            terminal,
            pids_data,
            host,
            operate,
            options=session_options(args),
            transport_label=transport_label,
        )
    finally:
        if terminal is not None:
            await terminal.close()
        if conn.reboot:
            reboot_after_session(transport)
    return rc


# The live commands that help a user find out which car they have, and so may run
# before one is chosen — but only while they record nothing.
_IDENTIFYING_COMMANDS = frozenset({"discover", "disc", "identity", "id"})


def _require_car(args) -> None:
    """Refuse a live command until the profile's car is named (see require_car)."""
    from canair.cli.car import require_car

    saving = bool(getattr(args, "save", False))
    if getattr(args, "command", None) in _IDENTIFYING_COMMANDS and not saving:
        return
    require_car("record captures" if saving else "talk to the car")


def run_live(args, operation: LiveOperation) -> int:
    """Acquire the device lock and run ``async_main`` for a live subcommand."""
    _require_car(args)
    lock = WiCANLock()
    lock.acquire(force=args.force, fail=messages.fail)

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
                result = asyncio.run(async_main(args, operation))
            finally:
                watchdog.stop()
    except KeyboardInterrupt:
        with contextlib.suppress(OSError):
            # The terminal may be gone (SIGHUP) — a failed write must not mask
            # the clean shutdown that already happened.
            print("\nInterrupted.", file=sys.stderr)
        return 0
    finally:
        lock.release()
    return result if isinstance(result, int) else 0
