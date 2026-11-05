"""``canair scan`` — the scan command group.

Five kinds of scan live under one command, one module per concern:

  * ``canair scan range <ECU>``      — sweep a PID/DID range on one ECU (the
    general-purpose scan, with friendly ``--service`` presets + a wizard;
    :mod:`.range_sweep`).
  * ``canair scan iocontrol <ECU>``  — IOControl discovery. Auto-selects UDS
    ``0x2F`` or KWP2000 ``0x30`` from the ECU's ``id_protocol`` and only ever sends
    the returnControlToECU probe (never an actuating option). On KWP2000 that probe
    is not proven to drive nothing, so the sweep confirms first (:mod:`.probes`).
  * ``canair scan routines <ECU>``   — SAFE RoutineControl (``0x31``) discovery
    (probes requestRoutineResults only; :mod:`.probes`).
  * ``canair scan sessions <ECU>``   — SAFE session-type discovery (:mod:`.probes`).

For convenience, a bare ``canair scan <ECU>`` (or ``canair scan`` with no args)
is treated as ``canair scan range …`` — see ``_inject_default_subcommand`` in
``src/canair/cli/__init__.py``.

Belongs: `canair scan`: each kind's parser, the range plan and wizard, the confirmation.
Not here: the sweeps over the car (`live/`) and their result sections (`yaml_edit/`).
Imports: `live` (its presets and handlers) and below.
"""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help

from .probes import add_iocontrol_parser, add_routines_parser, add_sessions_parser
from .range_sweep import add_range_parser

NAME = "scan"

# Subcommand names under ``canair scan``. Kept in sync with the ``scan`` entry in
# ``cli._GROUP_DEFAULTS`` (which injects "range" when the token after ``scan``
# isn't one of these).
SCAN_KINDS = ("range", "iocontrol", "routines", "sessions")


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Scan an ECU: range | iocontrol | routines | sessions",
        description="Scan an ECU. Choose a kind:\n"
        "  range      sweep a PID/DID range (general purpose)\n"
        "  iocontrol  IOControl discovery (UDS 0x2F; KWP2000 0x30 asks first)\n"
        "  routines   SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)\n"
        "  sessions   SAFE diagnostic session-type discovery (0x10, auto UDS/KWP2000)\n\n"
        "A bare `canair scan BMS` (or `canair scan` alone) is shorthand for "
        "`canair scan range …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair scan BMS                         # sweep a PID range on BMS (bare = range)
  canair scan range                       # interactive wizard (pick ECU/service/range)
  canair scan range IGPM --range BC01-BC0B  # scan a specific DID range
  canair scan iocontrol IGPM              # SAFE actuator (IOControl) discovery
  canair scan routines BCM                # SAFE routine discovery
  canair scan sessions BMS                # SAFE diagnostic session-type discovery

Run `canair scan <kind> --help` for the flags of each kind.""",
    )
    kinds = parser.add_subparsers(dest="scan_kind", metavar="<kind>")
    add_range_parser(kinds)
    add_iocontrol_parser(kinds)
    add_routines_parser(kinds)
    add_sessions_parser(kinds)
    parser.set_defaults(func=group_help("_scan_group_parser"), _scan_group_parser=parser)
    return parser
