"""Shared runtime for the live-device subcommands (read, monitor, scan, dtc, ...).

All live subcommands talk to the vehicle over the configured transport (raw
SLCAN-over-TCP by default, or an ELM327 terminal over the WiCAN WebSocket or a
plain TCP socket — see :mod:`canlib.transport.config`). They share one argument
namespace, one connection lifecycle, and one mode dispatcher. Each subcommand is a
thin argparse surface that populates the namespace and then calls
:func:`run_live`.

Private to the command layer, hence the underscore: it is CLI plumbing, not
library API. The parts that were neither — the mode dispatcher, ``wants_save``,
``split_ecus_by_protocol`` — have been pushed down to :mod:`canlib.modes.dispatch`,
:mod:`canlib.keepmode` and :mod:`canlib.ecus`, so nothing under ``canlib/`` imports
this any more.

Submodules, in the order a run passes through them:

* :mod:`.defaults`   — the argument namespace and every attribute's default.
* :mod:`.parser`     — the shared flags, and the namespace backfill.
* :mod:`.completers` — argcomplete callbacks for those arguments.
* :mod:`.steps`      — multi mini-language STEP handling (``@group`` expansion).
* :mod:`.connect`    — building/connecting the ELM327 terminal, and re-homing it.
* :mod:`.runtime`    — the device lock, signal handling, and one session's lifecycle.

This module re-exports the surface the subcommands import; the import-time
side effects below have to happen once, on any live command, so they live here.
"""

from __future__ import annotations

import importlib.util
import io
import sys

# Force line-buffered stdout so output appears immediately when piped.
# reconfigure() only exists on TextIOWrapper; guard so it's safe (and
# type-correct) when stdout/stderr are redirected to a plain stream.
if not sys.stdout.isatty():
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(line_buffering=True)

if importlib.util.find_spec("websockets") is None:  # pragma: no cover
    print("ERROR: websockets not installed. Run: pip3 install websockets", file=sys.stderr)
    sys.exit(1)

from canlib import load_pids

from .completers import (
    ecu_completer,
    param_completer,
    pid_completer,
    step_completer,
)
from .connect import build_elm_reconnector, connect_elm_terminal
from .defaults import CANAIR_DEFAULTS
from .parser import add_connection_args, finalize_live_parser
from .runtime import async_main, run, run_live
from .steps import STEP_VERBS, expand_step_groups, to_step

__all__ = [
    "CANAIR_DEFAULTS",
    "STEP_VERBS",
    "add_connection_args",
    "async_main",
    "build_elm_reconnector",
    "connect_elm_terminal",
    "ecu_completer",
    "expand_step_groups",
    "finalize_live_parser",
    "load_pids",
    "param_completer",
    "pid_completer",
    "run",
    "run_live",
    "step_completer",
    "to_step",
]
