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
        help="Import DBC broadcast signal definitions into the signals/ model",
    )
    p_dbc.add_argument("file", help="Path to a .dbc file")
    p_dbc.add_argument("--bus", help="Target bus (signals/<bus>.yaml; default: DBC filename)")
    p_dbc.add_argument("--tx-ecu", dest="tx_ecu", help="Annotate imported messages with this ECU")
    p_dbc.add_argument("--ids", help="Comma-separated arbitration IDs to import (default: all)")
    p_dbc.add_argument("--dry-run", action="store_true", help="Print the diff without writing")
    p_dbc.add_argument("--json", action="store_true", help="Machine-readable result")
    p_dbc.set_defaults(_import_kind="dbc", func=run)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    kind = getattr(args, "_import_kind", None)
    if kind == "can":
        return _run_can(args)
    if kind == "dbc":
        return _run_dbc(args)
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


def _dbc_to_signals(db, id_filter: set[int] | None, tx_ecu: str | None) -> dict:
    """Map a cantools Database to the linear signals/ import shape.

    ``{mid: {"name", "tx_ecu", "signals": {sig: {start_bit, length, byte_order,
    scale, offset, min, max, unit}}}}``. cantools byte_order (little_endian/
    big_endian) → little/big.
    """
    imported: dict = {}
    for msg in db.messages:
        if id_filter is not None and msg.frame_id not in id_filter:
            continue
        signals: dict = {}
        for s in msg.signals:
            fields = {
                "start_bit": int(s.start),
                "length": int(s.length),
                "byte_order": "big" if s.byte_order == "big_endian" else "little",
            }
            if s.scale is not None and s.scale != 1:
                fields["scale"] = s.scale
            if s.offset:
                fields["offset"] = s.offset
            if s.minimum is not None:
                fields["min"] = s.minimum
            if s.maximum is not None:
                fields["max"] = s.maximum
            if s.unit:
                fields["unit"] = s.unit
            signals[s.name] = fields
        if signals:
            imported[f"0x{msg.frame_id:X}"] = {
                "name": msg.name,
                "tx_ecu": tx_ecu,
                "signals": signals,
            }
    return imported


def _run_dbc(args) -> int:
    import json

    try:
        import cantools
    except ImportError:
        print("import dbc: cantools is required (pip install cantools).", file=sys.stderr)
        return 1

    path = Path(args.file)
    if not path.is_file():
        print(f"import dbc: no such file: {path}", file=sys.stderr)
        return 1
    try:
        db = cantools.database.load_file(str(path), strict=False)
    except Exception as e:  # cantools parse errors
        print(f"import dbc: could not parse {path.name}: {e}", file=sys.stderr)
        return 1

    id_filter = None
    if args.ids:
        id_filter = {int(t.strip(), 16) for t in args.ids.split(",") if t.strip()}
    imported = _dbc_to_signals(db, id_filter, args.tx_ecu)
    if not imported:
        print(
            f"import dbc: no messages to import from {path.name}"
            + (" (after --ids filter)" if args.ids else ""),
            file=sys.stderr,
        )
        return 1

    bus = args.bus or path.stem
    n_sig = sum(len(m["signals"]) for m in imported.values())

    if args.dry_run:
        if args.json:
            json.dump({"bus": bus, "messages": imported}, sys.stdout, indent=2)
            print()
            return 0
        print(
            f"import dbc (dry run) → signals/{bus}.yaml: {len(imported)} messages, {n_sig} signals"
        )
        for mid, m in list(imported.items())[:20]:
            print(
                f"  {mid} {m['name']}: {', '.join(list(m['signals'])[:6])}"
                + (" …" if len(m["signals"]) > 6 else "")
            )
        if len(imported) > 20:
            print(f"  … +{len(imported) - 20} more messages")
        print("Re-run without --dry-run to write.")
        return 0

    from canlib.profile import active
    from canlib.signals_edit import SignalsEditError, merge_bus

    try:
        out, written = merge_bus(bus, imported, profile=active())
    except SignalsEditError as e:
        print(f"import dbc: {e}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(
            {"bus": bus, "file": str(out), "messages": len(imported), "signals": written},
            sys.stdout,
            indent=2,
        )
        print()
        return 0
    print(f"Imported {len(imported)} messages ({written} signals) → {out}")
    print("Review/verify with `canair signals list` and `canair validate signals`.")
    return 0
