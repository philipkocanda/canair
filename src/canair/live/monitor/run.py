"""Live monitor — the run driver (entry point + poll loops).

:func:`mode_monitor` is the live monitor's entry point: it builds a
:class:`~canair.live.monitor.controller.MonitorController`, wires up recording/reconnect,
and drives either the Textual TUI (on a TTY) or the non-interactive poll loop
(:func:`_monitor_noninteractive`, piped/scripted runs), then reconciles the
``--save`` journal and closes the device on the way out.

Factored out of :mod:`canair.live.monitor.controller` so the controller class and the
process-level driver (signal handling, the poll loop, the TTY/non-TTY branch,
journal reconcile) are separate concerns. Callers import ``mode_monitor`` from here.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import TYPE_CHECKING

from canair.core.stop_signals import graceful_stop
from canair.render.console import console, escape
from canair.render.glyphs import FAIL

from .controller import MonitorController
from .options import MonitorOptions
from .record import _open_journal

if TYPE_CHECKING:
    from .reconnect import Reconnector


async def _monitor_noninteractive(controller: MonitorController) -> None:
    """No TTY: poll silently until a stop signal/disconnect (piped/scripted runs)."""
    stop_flag = {"v": False}

    def _handle_stop(_sig, _frame):
        stop_flag["v"] = True
        # Unblock any in-flight raw poll immediately so we don't wait out the
        # cycle's per-ECU timeouts (the slow-Ctrl-C / slow-`kill` cause).
        controller.interrupt()

    def _notice(msg: str) -> None:
        with contextlib.suppress(OSError):
            console(stderr=True).print(f"  [yellow]{escape(msg)}[/yellow]")

    # Handle interactive Ctrl-C (SIGINT), a `kill`/`pkill` or the lock
    # watchdog's self-abort (SIGTERM), and a vanished terminal (SIGHUP) the same
    # way — a graceful stop that reconciles the --save journal on exit.
    old_int = signal.signal(signal.SIGINT, _handle_stop)
    with graceful_stop(_handle_stop):
        try:
            while not stop_flag["v"] and not controller.disconnected:
                await controller.poll_once()
                if controller.disconnected:
                    # Try to re-home the dropped session (auto-failover / --wait)
                    # instead of exiting; the --save journal keeps recording across
                    # the gap since the controller (and its journal) is preserved.
                    if not await _attempt_reconnect(controller, stop_flag, _notice):
                        return
                    continue
                remaining = controller.seconds_until_due()
                while remaining > 0 and not stop_flag["v"] and not controller.disconnected:
                    await asyncio.sleep(min(remaining, 0.1))
                    remaining = controller.seconds_until_due()
        finally:
            signal.signal(signal.SIGINT, old_int)


async def _attempt_reconnect(controller: MonitorController, stop_flag: dict, notice) -> bool:
    """Re-home a dropped monitor session; return True to resume, False to stop.

    Returns False when there is no reconnector, when the bounded window expired
    (leaving :attr:`disconnected` set so ``mode_monitor`` reports the drop), or
    when the user stopped during the attempt (clearing :attr:`disconnected` so
    the run ends cleanly rather than as a failure).
    """
    if controller.reconnect is None:
        return False
    controller.reconnecting = True
    notice("⟳ connection dropped — reconnecting…")
    try:
        ok = await controller.reconnect(
            controller,
            controller.session_steps,
            stop=lambda: stop_flag["v"],
            notice=notice,
        )
    finally:
        controller.reconnecting = False
    if ok:
        return True
    if stop_flag["v"]:
        # User stopped mid-reconnect: a deliberate stop, not a failure.
        controller.disconnected = False
    return False


async def mode_monitor(
    terminal,
    monitor: MonitorOptions,
    pids_data: dict,
    *,
    raw_client=None,
    transport_type: str | None = None,
    reconnect: Reconnector | None = None,
):
    """Live-refresh ECU signal monitor.

    On a TTY this runs ``monitor.interactive`` (the CLI passes the Textual monitor app:
    scrollable, in-place value updates, mouse + keyboard). Otherwise, or without
    one, it polls silently until Ctrl+C and prints the final values. Sessions are
    opened once (from ``monitor.session_steps``) and kept alive with background keepalives.

    Args:
        terminal:       Connected terminal (WiCANTerminal), or None with ``raw_client``.
        monitor:        What to poll and how to show and record it
                        (:class:`~canair.live.monitor.options.MonitorOptions`): the
                        steps, the poll interval and schedule, the backoff, the
                        keep-mode, ``--save`` and its label/state/notes (auto-suggested
                        when omitted), the rulers, the byte notation and the screen
                        to run on a TTY.
        pids_data:      Loaded PID definitions.
        raw_client:     The pipelined raw client, for the ``slcan-tcp`` path.
        transport_type: The transport label recorded with saved captures.
        reconnect:      Re-homes a dropped session.

    TUI keys come from the shared role registry (:mod:`canair.tui.keys`); press
    ``?`` in the app for the live cheat-sheet rather than trusting a copy here. A
    blinking ``● REC`` in the status line marks an active --save recording; a ``sess``
    status item shows each ECU's currently-open diagnostic session type; each
    signal row shows a small circle (green when fresh, grey when stale).
    """
    from ...profile import active
    from ...states import parse_states

    captures_dir = active().captures_dir
    states = parse_states(monitor.vehicle_states)
    label, notes = monitor.label, monitor.notes
    controller = MonitorController(terminal, monitor, pids_data, raw_client=raw_client)
    controller.captures_dir = captures_dir
    # Seed the header's segment metadata from the initial --label/--state/--notes.
    controller.recorder.session_label = label or ""
    controller.recorder.session_states = list(states or [])
    controller.recorder.session_notes = notes or ""
    # Resolve the transport label for saved-capture provenance: prefer the caller's
    # explicit value, else fall back to the active client's own diag label.
    diag = controller.diag_recorder()
    controller.transport_type = transport_type or (diag.transport if diag is not None else None)
    # Mid-session reconnect / auto-failover: the poll loops re-home a dropped
    # session via this reconnector (replaying session_steps to re-open sessions).
    controller.reconnect = reconnect
    controller.session_steps = monitor.session_steps

    # --save: open the write-ahead journal up front so every polled payload is
    # durably recorded as it arrives. On a clean stop we reconcile it into a
    # capture file; a disconnect/crash leaves it on disk for `--recover`.
    if monitor.save:
        controller.recorder.journal = _open_journal(controller, label, states, notes)
        journal_label = label or controller.view.query_label() or "Monitor session"
        print(
            f"  --save: journaling to {controller.recorder.journal.path.name} "
            f"(label: {journal_label!r}); auto-saves on stop. "
            "Press 's' to edit label/states/notes, 'n' to start a new segment.",
            file=sys.stderr,
        )

    try:
        await controller.setup(monitor.session_steps)

        if monitor.interactive is not None and sys.stdout.isatty():
            await monitor.interactive(controller)
        else:
            await _monitor_noninteractive(controller)

        if controller.disconnected:
            link = "raw CAN bus" if controller.raw else "WebSocket"
            with contextlib.suppress(OSError):
                err = console(stderr=True)
                err.print(f"\n  [bold red]{FAIL} {link} disconnected[/bold red]")
                err.print(f"  [red]Stopped after {controller.cycle} cycles.[/red]\n")
            raise ConnectionError(f"{link} disconnected")

        # Print the final values so a stopped session leaves them in scrollback
        # (best-effort: the terminal may be gone after a SIGHUP).
        with contextlib.suppress(OSError):
            console().print(controller.renderer.render())
            print("  Monitoring stopped.", file=sys.stderr)

    finally:
        # Replay the save banners the TUI swallowed (an in-run 's' save / 'n'
        # rotate wrote a real capture file while Textual owned stdout). The app —
        # and any modal — is gone by now, so this is the first moment those
        # destinations can actually reach the user. Writing may fail outright if
        # the terminal vanished (SIGHUP), which must not cost us the journal
        # reconcile below.
        with contextlib.suppress(OSError):
            for line in controller.recorder.drain_deferred_saves():
                print(line, file=sys.stderr)
        # Reconcile the journal even on disconnect/exception (this is the fix for
        # the old bug where a dropped connection lost the whole --save session).
        if controller.recorder.journal is not None:
            with contextlib.suppress(Exception):
                # If no state was set explicitly (flag or the TUI dialog), fall
                # back to the union of states auto-suggested across the run's span
                # — not just the state active at exit, so a run that charged then
                # went idle still reconciles as `charging`.
                if not states and not controller.recorder.state_explicit:
                    backfill = controller.recorder._backfill_states()
                    if backfill:
                        controller.recorder.journal.update_meta(vehicle_states=backfill)
                # Stamp the session with its transport data-quality footprint
                # (drops/errors/exchanges) so the capture's provenance is recorded.
                quality = controller.recorder.segment_quality()
                if quality is not None:
                    controller.recorder.journal.update_meta(quality=quality)
                # reconcile() prints its own "Saved N capture(s) to <full path>"
                # banner per written day-file — stdout is real again here, so no
                # extra (and potentially last-file-only) line is needed.
                controller.recorder.journal.reconcile()
        await controller.close()
