#!/usr/bin/env python3
"""``canair signals`` — view/edit broadcast signal definitions (domain B).

The frame-domain analogue of ``canair pids``: manage the ``signals/<bus>.yaml``
sidecar (arbitration ID → named linear signals) with surgical, validated,
comment-preserving edits (via :mod:`canlib.signals_edit`). Never hand-edit the
sidecar. See ``plans/2026-07-24-raw-can-analysis.md``.

  canair signals                     # list all broadcast signals
  canair signals list [BUS]          # list (optionally one bus)
  canair signals upsert BUS 0x386 WHL_SPD_FL --start-bit 0 --length 14 \\
      --byte-order little --scale 0.03125 --unit km/h
  canair signals rm BUS 0x386 WHL_SPD_FL
"""

from __future__ import annotations

import argparse
import json
import sys

NAME = "signals"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="View/edit broadcast signal definitions (signals/<bus>.yaml)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="signals_command")

    p_list = sub.add_parser("list", help="List broadcast signals (optionally one bus)")
    p_list.add_argument("bus", nargs="?", help="Restrict to one bus (signals/<bus>.yaml)")
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")
    p_list.set_defaults(_signals_func=cmd_list)

    p_up = sub.add_parser("upsert", help="Add or update a broadcast signal")
    p_up.add_argument("bus", help="Bus name (signals/<bus>.yaml)")
    p_up.add_argument("arb_id", help="Arbitration ID, e.g. 0x386")
    p_up.add_argument("name", help="Signal name, e.g. WHL_SPD_FL")
    p_up.add_argument("--start-bit", type=int, required=True, dest="start_bit")
    p_up.add_argument("--length", type=int, required=True)
    p_up.add_argument(
        "--byte-order", choices=["little", "big"], default="little", dest="byte_order"
    )
    p_up.add_argument("--scale", type=float)
    p_up.add_argument("--offset", type=float)
    p_up.add_argument("--min", type=float)
    p_up.add_argument("--max", type=float)
    p_up.add_argument("--unit")
    p_up.add_argument("--verified", dest="verified", action="store_true", default=None)
    p_up.add_argument("--unverified", dest="verified", action="store_false")
    p_up.add_argument("--notes")
    p_up.add_argument("--msg-name", dest="msg_name", help="Message name (DBC BO_ name)")
    p_up.add_argument("--tx-ecu", dest="tx_ecu", help="Transmitting ECU (annotation)")
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


def _load_all(bus_filter: str | None):
    """(bus, data) for each signals/<bus>.yaml (optionally one bus)."""
    import yaml

    from canlib.profile import active

    sig_dir = active().signals_dir
    if not sig_dir.exists():
        return []
    out = []
    for path in sorted(sig_dir.glob("*.yaml")):
        bus = path.stem
        if bus_filter and bus != bus_filter:
            continue
        out.append((bus, yaml.safe_load(path.read_text()) or {}))
    return out


def cmd_list(args) -> int:
    buses = _load_all(getattr(args, "bus", None))
    if getattr(args, "json", False):
        print(json.dumps(dict(buses), indent=2))
        return 0
    total = 0
    if not buses:
        print(
            "  No broadcast signals defined (signals/ is empty). Add with "
            "`canair signals upsert` or `canair import dbc`."
        )
        return 0
    for bus, data in buses:
        messages = data.get("messages") or {}
        nsig = sum(len((m or {}).get("signals") or {}) for m in messages.values())
        total += nsig
        print(f"\n  {_BOLD}{bus}{_RESET} {_DIM}({len(messages)} messages, {nsig} signals){_RESET}")
        for mid, msg in messages.items():
            msg = msg or {}
            name = f" {msg['name']}" if msg.get("name") else ""
            tx = f" {_DIM}[{msg['tx_ecu']}]{_RESET}" if msg.get("tx_ecu") else ""
            print(f"    {_CYAN}{mid}{_RESET}{name}{tx}")
            for sname, sig in (msg.get("signals") or {}).items():
                sig = sig or {}
                v = f" {_GREEN}✓{_RESET}" if sig.get("verified") else ""
                unit = f" {sig['unit']}" if sig.get("unit") else ""
                sc = sig.get("scale")
                scale = f" ×{sc}" if sc not in (None, 1) else ""
                print(
                    f"      {sname}{v}  {_DIM}bit {sig.get('start_bit')}+{sig.get('length')} "
                    f"{sig.get('byte_order', 'little')}{scale}{unit}{_RESET}"
                )
    print()
    return 0


def cmd_upsert(args) -> int:
    from canlib.signals_edit import SignalsEditError, upsert_signal

    try:
        path = upsert_signal(
            args.bus,
            args.arb_id,
            args.name,
            start_bit=args.start_bit,
            length=args.length,
            byte_order=args.byte_order,
            scale=args.scale,
            offset=args.offset,
            min=args.min,
            max=args.max,
            unit=args.unit,
            verified=args.verified,
            notes=args.notes,
            msg_name=args.msg_name,
            tx_ecu=args.tx_ecu,
        )
    except SignalsEditError as e:
        print(f"signals upsert: {e}", file=sys.stderr)
        return 1
    print(f"{_GREEN}✓{_RESET} {args.bus}: {args.arb_id} {args.name} → {path.name}")
    return 0


def cmd_rm(args) -> int:
    from canlib.signals_edit import SignalsEditError, remove_signal

    try:
        path = remove_signal(args.bus, args.arb_id, args.name)
    except SignalsEditError as e:
        print(f"signals rm: {e}", file=sys.stderr)
        return 1
    print(f"{_GREEN}✓{_RESET} removed {args.arb_id} {args.name} from {path.name}")
    return 0
