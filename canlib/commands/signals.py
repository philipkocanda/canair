#!/usr/bin/env python3
"""``canair signals`` — view/edit broadcast signal definitions (domain B).

The broadcast-frame (domain-B) authoring counterpart to ``canair pids`` (which
authors domain-A diagnostic UDS PID params): manage the ``signals/<bus>.yaml``
sidecar (arbitration ID → named linear signals) with surgical, validated,
comment-preserving edits (via :mod:`canlib.signals_edit`). Never hand-edit the
sidecar. See ``plans/2026-07-24-raw-can-analysis.md``.

  canair signals                     # list all broadcast signals
  canair signals list [BUS]          # list (optionally one bus)
  canair signals upsert BUS 0x386 WHL_SPD_FL --start-bit 0 --length 14 \\
      --byte-order little --scale 0.03125 --unit km/h
  canair signals upsert BUS 0x386 WHL_SPD_FL --verified   # change one field
  canair signals rm BUS 0x386 WHL_SPD_FL
"""

from __future__ import annotations

import argparse
import json
import sys

from canlib import ansi
from canlib.edit_echo import echo_edit
from canlib.signals_edit import CLEARABLE_SIGNAL_FIELDS

NAME = "signals"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Author broadcast signal defs in signals/ (domain B; see `pids` for domain A)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="signals_command")

    p_list = sub.add_parser("list", help="List broadcast signals (optionally one bus)")
    p_list.add_argument("bus", nargs="?", help="Restrict to one bus (signals/<bus>.yaml)")
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")
    p_list.set_defaults(_signals_func=cmd_list)

    p_up = sub.add_parser(
        "upsert",
        help="Add or update a broadcast signal",
        description=(
            "Add a broadcast signal, or change one. A new signal needs --start-bit and "
            "--length; on an existing one, only the options you pass change and the rest "
            "keep their stored values. An empty --unit/--source/--notes, or --clear "
            "FIELD, removes that field."
        ),
    )
    p_up.add_argument("bus", help="Bus name (signals/<bus>.yaml)")
    p_up.add_argument("arb_id", help="Arbitration ID, e.g. 0x386")
    p_up.add_argument("name", help="Signal name, e.g. WHL_SPD_FL")
    p_up.add_argument(
        "--start-bit",
        type=int,
        dest="start_bit",
        help="First bit, DBC numbering: the LSB for little-endian, the MSB for big-endian "
        "(required for a new signal)",
    )
    p_up.add_argument("--length", type=int, help="Length in bits (required for a new signal)")
    p_up.add_argument(
        "--byte-order",
        choices=["little", "big"],
        dest="byte_order",
        help="Byte order (DBC @1 / @0; a new signal defaults to little)",
    )
    sign = p_up.add_mutually_exclusive_group()
    sign.add_argument(
        "--signed",
        dest="signed",
        action="store_true",
        default=None,
        help="Two's-complement signed raw value (DBC @0-/@1-)",
    )
    sign.add_argument(
        "--unsigned", dest="signed", action="store_false", help="Unsigned raw value (default)"
    )
    kind = p_up.add_mutually_exclusive_group()
    kind.add_argument(
        "--float",
        dest="is_float",
        action="store_true",
        default=None,
        help="IEEE 754 float raw value, 32 or 64 bits (DBC SIG_VALTYPE_)",
    )
    kind.add_argument(
        "--integer", dest="is_float", action="store_false", help="Integer raw value (default)"
    )
    p_up.add_argument("--scale", type=float, help="physical = raw * scale + offset (default 1)")
    p_up.add_argument("--offset", type=float, help="Default 0")
    p_up.add_argument("--min", type=float)
    p_up.add_argument("--max", type=float)
    p_up.add_argument("--unit")
    ver = p_up.add_mutually_exclusive_group()
    ver.add_argument("--verified", dest="verified", action="store_true", default=None)
    ver.add_argument("--unverified", dest="verified", action="store_false")
    p_up.add_argument("--source", help="Provenance (DBC, log file, or RE evidence)")
    p_up.add_argument("--notes")
    p_up.add_argument(
        "--clear",
        action="append",
        default=[],
        choices=CLEARABLE_SIGNAL_FIELDS,
        metavar="FIELD",
        help=f"Remove a field (repeatable): {', '.join(CLEARABLE_SIGNAL_FIELDS)}",
    )
    p_up.add_argument("--msg-name", dest="msg_name", help="Message name (DBC BO_ name)")
    p_up.add_argument("--tx-ecu", dest="tx_ecu", help="Transmitting ECU (annotation)")
    p_up.add_argument(
        "--msg-length",
        type=int,
        dest="msg_length",
        help="Message data length in bytes (DBC DLC): 0-8, or a CAN FD size "
        "(12, 16, 20, 24, 32, 48, 64)",
    )
    fmt = p_up.add_mutually_exclusive_group()
    fmt.add_argument(
        "--extended",
        dest="extended",
        action="store_true",
        default=None,
        help="Extended (29-bit) frame; only needed for an ID at or below 0x7FF",
    )
    fmt.add_argument(
        "--standard",
        dest="extended",
        action="store_false",
        help="Standard (11-bit) frame (the default for an ID at or below 0x7FF)",
    )
    fd = p_up.add_mutually_exclusive_group()
    fd.add_argument(
        "--fd",
        dest="fd",
        action="store_true",
        default=None,
        help="CAN FD frame; only needed for a frame of 8 bytes or fewer",
    )
    fd.add_argument(
        "--classic",
        dest="fd",
        action="store_false",
        help="Classic CAN frame (the default up to 8 bytes)",
    )
    p_up.set_defaults(_signals_func=cmd_upsert)

    p_rm = sub.add_parser("rm", help="Remove a broadcast signal")
    p_rm.add_argument("bus")
    p_rm.add_argument("arb_id", help="Arbitration ID, e.g. 0x386")
    p_rm.add_argument("name")
    p_rm.set_defaults(_signals_func=cmd_rm)

    parser.set_defaults(func=run, _signals_func=cmd_list, bus=None, json=False)
    return parser


def run(args) -> int:
    return args._signals_func(args)


def cmd_list(args) -> int:
    from canlib.signals import load_signals

    docs = load_signals(getattr(args, "bus", None))
    if getattr(args, "json", False):
        print(json.dumps({d.bus: d.data for d in docs}, indent=2))
        return 0
    total = 0
    if not docs:
        print(
            "  No broadcast signals defined (signals/ is empty). Add with "
            "`canair signals upsert` or `canair import dbc`."
        )
        return 0
    for bus, data in ((d.bus, d.data) for d in docs):
        messages = data.get("messages") or {}
        nsig = sum(len((m or {}).get("signals") or {}) for m in messages.values())
        total += nsig
        print(
            f"\n  {ansi.BOLD}{bus}{ansi.RESET} "
            f"{ansi.DIM}({len(messages)} messages, {nsig} signals){ansi.RESET}"
        )
        for mid, msg in messages.items():
            msg = msg or {}
            name = f" {msg['name']}" if msg.get("name") else ""
            facts = _frame_facts(mid, msg)
            if facts:
                name += f" {ansi.DIM}({', '.join(facts)}){ansi.RESET}"
            tx = f" {ansi.DIM}[{msg['tx_ecu']}]{ansi.RESET}" if msg.get("tx_ecu") else ""
            print(f"    {ansi.CYAN}{mid}{ansi.RESET}{name}{tx}")
            for sname, sig in (msg.get("signals") or {}).items():
                sig = sig or {}
                v = f" {ansi.GREEN}✓{ansi.RESET}" if sig.get("verified") else ""
                unit = f" {sig['unit']}" if sig.get("unit") else ""
                sc = sig.get("scale")
                scale = f" ×{sc}" if sc not in (None, 1) else ""
                signed = " signed" if sig.get("signed") else ""
                kind = " float" if sig.get("float") else ""
                if sig.get("type") not in (None, "numeric"):
                    kind += f" {sig['type']}"
                print(
                    f"      {sname}{v}  {ansi.DIM}bit {sig.get('start_bit')}+{sig.get('length')} "
                    f"{sig.get('byte_order', 'little')}{signed}{kind}{scale}{unit}{ansi.RESET}"
                )
    print()
    return 0


def _frame_facts(mid: object, msg: dict) -> list[str]:
    """The declared length and any non-default frame format, for a message's list line."""
    from canlib.validation.signals import (
        default_extended,
        message_is_extended,
        message_is_fd,
        message_length,
    )

    facts = [f"{msg['length']} B"] if msg.get("length") is not None else []
    try:
        frame_id = int(str(mid), 16)
    except ValueError:
        return facts
    if message_is_extended(frame_id, msg) and not default_extended(frame_id):
        facts.append("extended")
    ln = message_length(msg)
    if ln is not None and message_is_fd(msg, ln):
        facts.append("FD")
    return facts


def cmd_upsert(args) -> int:
    from canlib.profile import active, require_writable_definitions
    from canlib.signals_edit import SignalsEditError, upsert_signal

    require_writable_definitions()
    try:
        path = upsert_signal(
            args.bus,
            args.arb_id,
            args.name,
            start_bit=args.start_bit,
            length=args.length,
            byte_order=args.byte_order,
            signed=args.signed,
            is_float=args.is_float,
            scale=args.scale,
            offset=args.offset,
            min=args.min,
            max=args.max,
            unit=args.unit,
            verified=args.verified,
            source=args.source,
            notes=args.notes,
            clear=args.clear,
            msg_name=args.msg_name,
            tx_ecu=args.tx_ecu,
            msg_length=args.msg_length,
            extended=args.extended,
            fd=args.fd,
        )
    except SignalsEditError as e:
        print(f"signals upsert: {e}", file=sys.stderr)
        return 1
    echo_edit(
        f"[{ansi.CYAN}{active().name}{ansi.RESET}] {args.bus}: {args.arb_id} {args.name}", path
    )
    return 0


def cmd_rm(args) -> int:
    from canlib.profile import active, require_writable_definitions
    from canlib.signals_edit import SignalsEditError, remove_signal

    require_writable_definitions()
    try:
        path = remove_signal(args.bus, args.arb_id, args.name)
    except SignalsEditError as e:
        print(f"signals rm: {e}", file=sys.stderr)
        return 1
    echo_edit(f"[{ansi.CYAN}{active().name}{ansi.RESET}] removed {args.arb_id} {args.name}", path)
    return 0
