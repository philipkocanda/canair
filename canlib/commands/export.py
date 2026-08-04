#!/usr/bin/env python3
"""``canair export`` — export canair data to interchange formats.

Currently: ``export dbc`` writes the profile's broadcast signal definitions
(``signals/<bus>.yaml``, domain B) to a DBC via cantools, so they're consumable
by SavvyCAN / cabana / cantools / the Wireshark CAN dissector. Domain-A decoded
UDS parameter export (CSV/JSON) is tracked in the import/export plan.
See ``plans/2026-07-24-raw-can-analysis.md``.
"""

from __future__ import annotations

import argparse
import sys

NAME = "export"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Export canair data to interchange formats (DBC)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="export_command", required=True)

    p_dbc = sub.add_parser("dbc", help="Export broadcast signal defs (signals/) to a DBC")
    p_dbc.add_argument("--bus", help="Only this bus (signals/<bus>.yaml); default: all")
    p_dbc.add_argument(
        "--verified-only",
        action="store_true",
        dest="verified_only",
        help="Export only signals marked verified",
    )
    p_dbc.add_argument("-o", "--output", metavar="FILE", help="Write to FILE (default: stdout)")
    p_dbc.set_defaults(_export_kind="dbc", func=run)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if getattr(args, "_export_kind", None) == "dbc":
        return _run_dbc(args)
    print("canair export: pick a subcommand (dbc). See --help.", file=sys.stderr)
    return 2


def _run_dbc(args) -> int:
    try:
        from cantools.database import Database, Message, Signal
        from cantools.database.conversion import BaseConversion
    except ImportError:
        print("export dbc: cantools is required (pip install cantools).", file=sys.stderr)
        return 1

    from canlib.signals import load_signals, signals_dir

    if not signals_dir().is_dir():
        print("export dbc: no signals/ to export.", file=sys.stderr)
        return 1
    docs = load_signals(args.bus)
    if not docs:
        print("export dbc: no matching signals files.", file=sys.stderr)
        return 1

    messages: list = []
    for data in (d.data for d in docs):
        for mid, msg in (data.get("messages") or {}).items():
            msg = msg or {}
            sigs = []
            max_bit = 0
            for sname, sig in (msg.get("signals") or {}).items():
                sig = sig or {}
                if args.verified_only and not sig.get("verified"):
                    continue
                start = int(sig.get("start_bit", 0))
                length = int(sig.get("length", 1))
                max_bit = max(max_bit, start + length)
                conv = BaseConversion.factory(
                    scale=sig.get("scale", 1) or 1,
                    offset=sig.get("offset", 0) or 0,
                    is_float=False,
                )
                sigs.append(
                    Signal(
                        name=sname,
                        start=start,
                        length=length,
                        byte_order="big_endian"
                        if sig.get("byte_order") == "big"
                        else "little_endian",
                        conversion=conv,
                        minimum=sig.get("min"),
                        maximum=sig.get("max"),
                        unit=sig.get("unit"),
                    )
                )
            if not sigs:
                continue
            messages.append(
                Message(
                    frame_id=int(str(mid), 16),
                    name=msg.get("name") or f"MSG_{str(mid).upper().replace('0X', '')}",
                    length=max(1, (max_bit + 7) // 8),
                    signals=sigs,
                    strict=False,  # tolerate overlapping signals (real DBCs have them)
                )
            )

    if not messages:
        print("export dbc: nothing to export (no signals matched).", file=sys.stderr)
        return 1

    db = Database(messages=messages, strict=False)
    dbc_text = db.as_dbc_string()
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(dbc_text)
        print(f"Exported {len(messages)} messages → {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(dbc_text)
    return 0
