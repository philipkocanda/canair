"""Shared runtime for the live-device subcommands (read, monitor, scan, dtc, ...).

All live subcommands talk to the vehicle over the configured transport (raw
SLCAN-over-TCP by default, or an ELM327 terminal over the WiCAN WebSocket or a
plain TCP socket — see :mod:`canair.transport.config`). They share one connection
lifecycle. Each subcommand is a thin argparse surface that picks its
:class:`~.runtime.LiveOperation` and calls :func:`run_live` with it.

It is CLI plumbing, not library API (the live operations themselves are :mod:`canair.live`).
The parts that were neither — the session (:mod:`canair.live.session`), ``wants_save``,
``split_ecus_by_protocol`` — live in :mod:`canair.live`, :mod:`canair.captures.keepmode` and
:mod:`canair.ecus`, so nothing outside ``cli/`` imports this.

Submodules, in the order a run passes through them:

* :mod:`.parser`     — the shared flags, and :func:`live_command`.
* :mod:`.handlers`   — what each subcommand does once connected (the only code that reads
  the parsed flags on its way to a ``mode_*``).
* :mod:`.completers` — argcomplete callbacks for those arguments.
* :mod:`.steps`      — multi mini-language STEP handling (``@group`` expansion).
* :mod:`.connect`    — building/connecting the ELM327 terminal, and re-homing it.
* :mod:`.runtime`    — the device lock, signal handling, and one session's lifecycle.

This module re-exports the surface the subcommands import; the import-time
side effects below have to happen once, on any live command, so they live here.

Belongs: the live-command runtime: the shared connection flags, `live_command`, the session
lifecycle and the handlers.
Not here: what an operation does on the car (`live/`) and the session error policy
(`live/session.py`).
Imports: `live`, `transport`, `device` and below.
"""

from __future__ import annotations

import importlib.util
import io
import sys

from canair.render import messages

# Force line-buffered stdout so output appears immediately when piped.
# reconfigure() only exists on TextIOWrapper; guard so it's safe (and
# type-correct) when stdout/stderr are redirected to a plain stream.
if not sys.stdout.isatty():
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(line_buffering=True)

if importlib.util.find_spec("websockets") is None:  # pragma: no cover
    messages.fail("websockets not installed. Run: pip3 install websockets")

from canair.ecus.pids import load_pids

from .completers import (
    ecu_completer,
    param_completer,
    pid_completer,
    step_completer,
)
from .connect import build_elm_reconnector, connect_elm_terminal
from .operation import LiveOperation
from .parser import add_connection_args, live_command
from .runtime import async_main, run_live
from .steps import STEP_VERBS, expand_step_groups, report_merged_selectors, to_step

__all__ = [
    "STEP_VERBS",
    "LiveOperation",
    "add_connection_args",
    "async_main",
    "build_elm_reconnector",
    "connect_elm_terminal",
    "ecu_completer",
    "expand_step_groups",
    "live_command",
    "load_pids",
    "param_completer",
    "pid_completer",
    "report_merged_selectors",
    "run_live",
    "step_completer",
    "to_step",
]
