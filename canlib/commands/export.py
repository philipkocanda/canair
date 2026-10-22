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
        from cantools.database import Database
    except ImportError:
        print("export dbc: cantools is required (pip install cantools).", file=sys.stderr)
        return 1

    from canlib.signals import load_signals, signals_dir
    from canlib.validation.signals import check_signals_doc

    if not signals_dir().is_dir():
        print("export dbc: no signals/ to export.", file=sys.stderr)
        return 1
    docs = load_signals(args.bus)
    if not docs:
        print("export dbc: no matching signals files.", file=sys.stderr)
        return 1
    # cantools raises on what the validator rejects (an over-long ID, a short frame),
    # so refuse an invalid map up front, in the validator's words.
    for doc in docs:
        errors, _ = check_signals_doc(doc.data)
        if errors:
            print(f"export dbc: {doc.path.name} is invalid:", file=sys.stderr)
            for e in errors[:10]:
                print(f"  {e}", file=sys.stderr)
            print("  Fix it (see `canair validate signals`), then re-run.", file=sys.stderr)
            return 1

    messages = [
        m
        for data in (d.data for d in docs)
        for mid, msg in (data.get("messages") or {}).items()
        if (m := _dbc_message(mid, msg or {}, verified_only=args.verified_only)) is not None
    ]
    if not messages:
        print("export dbc: nothing to export (no signals matched).", file=sys.stderr)
        return 1

    db = Database(messages=messages, strict=False, dbc_specifics=_dbc_specifics(messages))
    dbc_text = db.as_dbc_string()
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(dbc_text)
        print(f"Exported {len(messages)} messages → {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(dbc_text)
    return 0


def _dbc_message(mid: object, msg: dict, *, verified_only: bool):
    """One validated ``signals/`` message as a cantools ``Message`` (None if no signals)."""
    from cantools.database import Message

    from canlib.validation.signals import message_is_extended, message_is_fd, message_length

    sigs = {
        name: sig or {}
        for name, sig in (msg.get("signals") or {}).items()
        if not verified_only or (sig or {}).get("verified")
    }
    if not sigs:
        return None
    frame_id = int(str(mid), 16)
    # Without a declared DLC, the smallest legal one holding the signals exported.
    length = message_length({**msg, "signals": sigs})
    if length is None:  # unreachable for a validated map
        raise ValueError(f"{mid}: length is not an integer")
    return Message(
        frame_id=frame_id,
        is_extended_frame=message_is_extended(frame_id, msg),
        is_fd=message_is_fd(msg, length),
        name=msg.get("name") or f"MSG_{str(mid).upper().replace('0X', '')}",
        length=length,
        signals=[_dbc_signal(name, sig) for name, sig in sigs.items()],
        strict=False,  # tolerate overlapping signals (real DBCs have them)
    )


def _dbc_signal(name: str, sig: dict):
    from collections import OrderedDict

    from cantools.database import Signal
    from cantools.database.conversion import BaseConversion
    from cantools.database.namedsignalvalue import NamedSignalValue

    is_float = sig.get("float") is True
    scale = sig.get("scale", 1)
    offset = sig.get("offset", 0)
    # canair's enum keys are the decoded value, a DBC VAL_ key the raw one: they agree
    # only for an unscaled integer signal, so only that one carries a value table.
    choices: OrderedDict[int, str | NamedSignalValue] | None = None
    if sig.get("type") == "enum" and not is_float and (scale, offset) == (1, 0):
        values = sig.get("values") or {}
        choices = OrderedDict((int(k), str(v)) for k, v in values.items()) or None
    return Signal(
        name=name,
        start=sig["start_bit"],
        length=sig["length"],
        byte_order="big_endian" if sig.get("byte_order") == "big" else "little_endian",
        is_signed=sig.get("signed") is True,
        conversion=BaseConversion.factory(
            scale=scale, offset=offset, choices=choices, is_float=is_float
        ),
        minimum=sig.get("min"),
        maximum=sig.get("max"),
        unit=sig.get("unit"),
    )


def _dbc_specifics(messages: list):
    """The DBC attribute definitions the export needs: ``VFrameFormat`` once a frame is FD.

    cantools writes a message's CAN FD frame format only when the database defines it.
    """
    if not any(m.is_fd for m in messages):
        return None
    from collections import OrderedDict

    from cantools.database.can.formats.dbc import ATTRIBUTE_DEFINITION_VFRAMEFORMAT
    from cantools.database.can.formats.dbc_specifics import DbcSpecifics

    return DbcSpecifics(
        attribute_definitions=OrderedDict(VFrameFormat=ATTRIBUTE_DEFINITION_VFRAMEFORMAT)
    )
