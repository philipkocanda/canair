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
Selection is a table, not a chain of ifs: :data:`_DISPATCH` pairs each handler
with the predicate that selects it, **in the original order** (order matters —
``multi`` + ``monitor`` must be tested before bare ``multi``). The handlers
themselves are grouped by family in the submodules, so the whole set of modes a
subcommand can reach is readable in one screen here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Awaitable, Callable

from canlib.keepmode import wants_save
from canlib.transport.protocol import Terminal

from . import actuators, diagnostics, elm_only, multi, reads, scans
from .ranges import parse_range

__all__ = ["dispatch_mode", "parse_range", "run_session_guarded"]

# A mode handler: it owns the whole subcommand once selected, and reports its own
# errors (several exit non-zero via sys.exit rather than returning).
Handler = Callable[..., Awaitable[None]]
# The predicate that selects a handler, over the parsed argument namespace.
Selector = Callable[[argparse.Namespace], bool]

# (handler, predicate) in selection order. Order is significant and preserved from
# the original if/elif chain — `multi` + `monitor` must be tested before bare
# `multi`, or a monitor run would dispatch as a one-shot. The final fallback (the
# interactive REPL) is not in the table; it runs when nothing matched.
_DISPATCH: tuple[tuple[Handler, Selector], ...] = (
    (multi.handle_monitor, lambda args: bool(args.multi and args.monitor)),
    (multi.handle_multi, lambda args: bool(args.multi)),
    (elm_only.handle_skm_wake, lambda args: bool(args.skm_wakeup)),
    (reads.handle_identity, lambda args: bool(args.identity)),
    (diagnostics.handle_dtc, lambda args: bool(args.dtc or getattr(args, "dtc_all", False))),
    (reads.handle_param, lambda args: bool(args.param)),
    (reads.handle_ecu, lambda args: bool(args.ecu)),
    (reads.handle_raw, lambda args: bool(args.raw)),
    (scans.handle_scan, lambda args: bool(args.scan)),
    (actuators.handle_iocontrol, lambda args: bool(args.iocontrol)),
    (actuators.handle_routines, lambda args: bool(args.routines)),
    (scans.handle_routines_scan, lambda args: bool(args.routines_scan is not None)),
    (scans.handle_iocontrol_scan, lambda args: bool(args.iocontrol_scan is not None)),
    (
        scans.handle_sessions_scan,
        lambda args: bool(getattr(args, "sessions_scan", None) is not None),
    ),
    (scans.handle_discover, lambda args: bool(args.discover)),
)


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
    and never reaches that branch, since ``raw_ops`` routes monitoring to its
    pipelined client first). Constructing the ELM one inside a transport-agnostic
    dispatcher both contradicted that claim and tied this module to the CLI.
    """
    for handler, selects in _DISPATCH:
        if selects(args):
            await handler(args, terminal, pids_data, host, reconnect=reconnect)
            return
    await elm_only.handle_interactive(args, terminal, pids_data, host, reconnect=reconnect)
