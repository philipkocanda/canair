#!/usr/bin/env python3
"""``canair import`` — bring external CAN data into the profile.

Front door for the raw-CAN broadcast domain (see
``plans/2026-07-24-raw-can-analysis.md``). Two subcommands:

- ``import can <FILE>``  — a raw broadcast-CAN frame log (``.asc``/``.blf``/
  python-can ``.csv``/candump ``.log``/``.trc``) → the native ``captures/can/``
  store + an ``index.yaml`` entry. Reads .asc/.blf/python-can .csv/candump/SavvyCAN GVRET.
- ``import dbc <FILE>``  — a DBC → the profile's ``signals/`` broadcast
  signal-definition model, via cantools, with a ``--dry-run`` diff.
  (Stage 4 — not yet implemented.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

NAME = "import"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Import external CAN data (raw frame logs, DBC) into the active profile",
        description=(
            "Import external CAN data into the active profile.\n\n"
            "  can   raw broadcast-CAN frame log (.asc/.blf/.csv/candump .log/.trc)\n"
            "        -> captures/can/ native store + index.yaml\n"
            "  dbc   DBC signal definitions -> the profile's signals/ model (Stage 4)\n\n"
            "Frame logs are stored verbatim and indexed (they are not exploded into\n"
            "the captures/*.yaml schema). SavvyCAN GVRET (.csv) is auto-detected by header."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="import_command", required=True)

    p_can = sub.add_parser(
        "can",
        help="Import a raw broadcast-CAN frame log into captures/can/",
    )
    p_can.add_argument("file", help="Path to a .asc/.blf/.csv/candump .log/.trc frame log")
    p_can.add_argument(
        "--format",
        choices=["auto", "asc", "blf", "csv", "log", "gvret"],
        default="auto",
        help="Log format (default: auto-detect by extension; .csv is sniffed for GVRET).",
    )
    p_can.add_argument("--label", help="Session label for the index entry")
    p_can.add_argument(
        "--state",
        help="Vehicle power state(s) while logging (comma-separated, e.g. 'driving')",
    )
    p_can.add_argument("--notes", help="Free-text notes for the index entry")
    p_can.add_argument("--source", help="Provenance (capture tool, or upstream URL)")
    p_can.add_argument("--bitrate", type=int, help="Bus bitrate in bit/s (e.g. 500000)")
    p_can.add_argument("--date", help="Log date YYYY-MM-DD (default: from log, else omitted)")
    p_can.add_argument(
        "--force", action="store_true", help="Overwrite an already-imported log of the same name"
    )
    p_can.add_argument("--json", action="store_true", help="Machine-readable result")
    p_can.set_defaults(_import_kind="can", func=run)

    p_dbc = sub.add_parser(
        "dbc",
        help="Import DBC signal definitions (Stage 4 — not yet implemented)",
    )
    p_dbc.add_argument("file", help="Path to a .dbc file")
    p_dbc.add_argument("--ecu", help="Associate imported messages with this ECU/bus")
    p_dbc.add_argument("--ids", help="Comma-separated arbitration IDs to import (default: all)")
    p_dbc.add_argument("--dry-run", action="store_true", help="Print the diff without writing")
    p_dbc.set_defaults(_import_kind="dbc", func=run)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    kind = getattr(args, "_import_kind", None)
    if kind == "can":
        return _run_can(args)
    if kind == "dbc":
        print(
            "canair import dbc: not yet implemented — planned for Stage 4 "
            "(DBC -> the profile's signals/ model).\n"
            "See plans/2026-07-24-raw-can-analysis.md.",
            file=sys.stderr,
        )
        return 2
    print("canair import: pick a subcommand (can | dbc). See --help.", file=sys.stderr)
    return 2


def _run_can(args) -> int:
    import json

    from canlib import can_logs
    from canlib.profile import active

    states = [s.strip() for s in (args.state or "").split(",") if s.strip()] or None
    try:
        result = can_logs.import_log(
            Path(args.file),
            active(),
            fmt=args.format,
            label=args.label,
            vehicle_states=states,
            notes=args.notes,
            bitrate=args.bitrate,
            source=args.source,
            date=args.date,
            force=args.force,
        )
    except can_logs.CanLogError as e:
        print(f"import can: {e}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(
            {"stored": str(result.stored_path), "index_entry": result.entry},
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    e = result.entry
    ids = e.get("id_set", [])
    id_preview = ", ".join(ids[:8]) + (f", +{len(ids) - 8} more" if len(ids) > 8 else "")
    print(f"Imported {e['file']} ({e['format']}) -> {result.stored_path}")
    print(
        f"  {e['frame_count']} frames, {len(ids)} distinct IDs" + (f": {id_preview}" if ids else "")
    )
    if e.get("date"):
        print(f"  date: {e['date']}")
    print("  Indexed in captures/can/index.yaml. List with `canair captures --can`.")
    return 0
