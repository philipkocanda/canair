#!/usr/bin/env python3
"""``canair import`` — bring external CAN data into the profile (scaffold).

Front door for the raw-CAN broadcast domain (see
``plans/2026-07-24-raw-can-analysis.md``). Two subcommands:

- ``import can <FILE>``  — a raw broadcast-CAN frame log (``.asc``/``.blf``/``.csv``/
  candump/GVRET) → the native ``captures/can/`` store + an ``index.yaml`` entry.
  (Implemented in Stage 1.)
- ``import dbc <FILE>``  — a DBC → the profile's ``signals/`` broadcast
  signal-definition model, via cantools, with a ``--dry-run`` diff.
  (Implemented in Stage 4.)

This module is the Stage-0 scaffold: it registers the command surface (so the
arg shape is designed and testable) but the handlers are not implemented yet —
each prints where it's headed and exits non-zero.
"""

from __future__ import annotations

import argparse
import sys

NAME = "import"

_PLANNED = {
    "can": (
        "Stage 1",
        "import a raw broadcast-CAN frame log into captures/can/ + index.yaml",
    ),
    "dbc": (
        "Stage 4",
        "import a DBC into the profile's signals/ broadcast signal-definition model",
    ),
}


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Import external CAN data (raw frame logs, DBC) — scaffold; see raw-CAN plan",
        description=(
            "Import external CAN data into the active profile.\n\n"
            "  can   raw broadcast-CAN frame log (.asc/.blf/.csv/candump/gvret)\n"
            "        → captures/can/ native store + index.yaml   (Stage 1)\n"
            "  dbc   DBC signal definitions → the profile's signals/ model (Stage 4)\n\n"
            "Scaffold only: the command surface is registered, but the handlers are\n"
            "not implemented yet (see plans/2026-07-24-raw-can-analysis.md)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="import_command", required=True)

    p_can = sub.add_parser(
        "can",
        help="Import a raw broadcast-CAN frame log (Stage 1 — not yet implemented)",
    )
    p_can.add_argument("file", help="Path to a .asc/.blf/.csv/candump/gvret log")
    p_can.add_argument(
        "--format",
        choices=["auto", "asc", "blf", "csv", "log", "gvret"],
        default="auto",
        help="Log format (default: auto-detect by extension)",
    )
    p_can.add_argument("--label", help="Session label for the index entry")
    p_can.add_argument("--state", help="Vehicle power state(s) while logging")
    p_can.add_argument("--notes", help="Free-text notes for the index entry")
    p_can.add_argument("--bitrate", type=int, help="Bus bitrate in bit/s (e.g. 500000)")
    p_can.set_defaults(_import_kind="can")

    p_dbc = sub.add_parser(
        "dbc",
        help="Import DBC signal definitions (Stage 4 — not yet implemented)",
    )
    p_dbc.add_argument("file", help="Path to a .dbc file")
    p_dbc.add_argument("--ecu", help="Associate imported messages with this ECU/bus")
    p_dbc.add_argument("--ids", help="Comma-separated arbitration IDs to import (default: all)")
    p_dbc.add_argument("--dry-run", action="store_true", help="Print the diff without writing")
    p_dbc.set_defaults(_import_kind="dbc")

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    kind = getattr(args, "_import_kind", None)
    stage, what = _PLANNED.get(kind, ("", ""))
    print(
        f"canair import {kind}: not yet implemented — planned for {stage} "
        f"({what}).\nSee plans/2026-07-24-raw-can-analysis.md.",
        file=sys.stderr,
    )
    return 2
