"""``canair bus`` — list and edit the profile's CAN bus segments.

Prints each physical CAN bus segment declared in the active profile's
``can_buses.yaml`` with its human name, description, bus speed (bitrate), and
the number of ECUs sitting on it (an ECU spanning two segments counts on each).
An ECU tagged with the gateway code ``ALL`` bridges every segment, so it is
counted on each declared bus (including the diagnostic bus), not just a lone
``ALL`` row; a segment marked ``private`` is skipped. Bus codes are vendor-specific.

Examples:
  canair bus            # table of buses + descriptions + ECU counts
  canair bus --json     # machine-readable
  canair bus upsert H-CAN --name "High-speed CAN" --private
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TypedDict

from canair.cli.commands._edit import edit_scope
from canair.cli.profile_context import active
from canair.ecus.can_buses import ALL_CODE, expand_bus_membership, load_can_buses
from canair.ecus.registry import load_ecus
from canair.render import ansi
from canair.yaml_edit.echo import echo_edit

NAME = "bus"


class BusRecord(TypedDict):
    """One CAN-bus row in the ``canair bus`` output / ``--json`` payload."""

    code: str
    name: str | None
    description: str | None
    bitrate: int | None
    private: bool
    ecus: int


# ANSI colors — emitted only when stdout is a TTY (piped output stays plain).
def _fmt_bitrate(bitrate: int | None) -> str:
    """Human-readable bus speed (e.g. ``500 kbit/s``); ``—`` when unrecorded."""
    if not bitrate:
        return "—"
    if bitrate % 1000 == 0:
        return f"{bitrate // 1000} kbit/s"
    return f"{bitrate} bit/s"


def _normalize_bus(value) -> list[str]:
    """Normalize an ECU ``can_bus`` field (list or scalar) to a list of codes."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List and edit the profile's CAN bus segments",
        description="List the active profile's CAN buses and ECU counts, or edit "
        "the mapping-form vocabulary in can_buses.yaml.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    sub = parser.add_subparsers(dest="bus_command")
    upsert = sub.add_parser("upsert", help="Add or update a CAN bus")
    upsert.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON"
    )
    upsert.add_argument("code", help="Bus code, e.g. H-CAN")
    upsert.add_argument("--name", help="Human-readable name")
    upsert.add_argument("--description", help="Description of the segment")
    upsert.add_argument("--bitrate", type=int, help="Bus speed in bit/s")
    access = upsert.add_mutually_exclusive_group()
    access.add_argument(
        "--private",
        dest="private",
        action="store_const",
        const=True,
        help="Mark a segment the gateway and OBD-II do not reach",
    )
    access.add_argument(
        "--public",
        dest="private",
        action="store_const",
        const=False,
        help="Mark a segment the gateway reaches",
    )
    upsert.set_defaults(private=None)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if getattr(args, "bus_command", None) == "upsert":
        return _upsert(args)
    from collections import Counter

    prof = active()
    buses = load_can_buses(prof)
    ecus = load_ecus(profile=active())
    declared_codes = [b.code for b in buses]
    private_codes = [b.code for b in buses if b.private]

    # Count ECUs per bus code. An ECU tagged with the gateway code (ALL) bridges
    # every declared non-private segment, so it is counted on each of them (not
    # just a lone ALL row) — see expand_bus_membership.
    per_bus: Counter = Counter()
    n_unbussed = 0
    n_gateway = 0
    for info in ecus.values():
        if not isinstance(info, dict):
            continue
        codes = _normalize_bus(info.get("can_bus"))
        if not codes:
            n_unbussed += 1
            continue
        if any(c.upper() == ALL_CODE for c in codes):
            n_gateway += 1
        for code in expand_bus_membership(codes, declared_codes, private_codes):
            per_bus[code] += 1

    # Codes referenced by an ECU but not declared in the vocabulary (surfaced so
    # a typo/undeclared segment isn't silently invisible).
    declared = {b.code for b in buses}
    undeclared = sorted(set(per_bus) - declared)

    records: list[BusRecord] = [
        {
            "code": b.code,
            "name": b.name or None,
            "description": b.description or None,
            "bitrate": b.bitrate,
            "private": b.private,
            "ecus": per_bus.get(b.code, 0),
        }
        for b in buses
    ]

    if args.json:
        json.dump(
            {
                "buses": records,
                "undeclared": [{"code": c, "ecus": per_bus[c]} for c in undeclared],
                "unbussed_ecus": n_unbussed,
                "gateway_ecus": n_gateway,
                "source": str(prof.can_buses_file),
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    if not buses and not undeclared:
        print(
            f"\n  No CAN buses declared for profile {ansi.c(prof.name, ansi.CYAN)} "
            f"{ansi.c('(no can_buses.yaml)', ansi.DIM)}.\n"
            f"  Declare the vocabulary in {prof.can_buses_file}.\n"
        )
        return 0

    print(
        f"\n  {ansi.c('CAN buses', ansi.BOLD)} — {len(buses)} segment(s) in {ansi.c(prof.name, ansi.CYAN)}\n"
    )
    w = max([4, *(len(r["code"]) for r in records)])
    header = f"{'CODE':<{w}} {'ECUS':>4}  {'NAME':<16} {'SPEED':<10} DESCRIPTION"
    print(f"  {ansi.c(header, ansi.DIM)}")
    for r in records:
        code = ansi.c(f"{r['code']:<{w}}", ansi.CYAN)
        n = r["ecus"]
        n_str = f"{n:>4}" if n else ansi.c(f"{0:>4}", ansi.YELLOW)
        name = str(r["name"] or "—")[:16]
        speed = _fmt_bitrate(r["bitrate"])
        desc = str(r["description"] or "")
        print(f"  {code} {n_str}  {name:<16} {speed:<10} {ansi.c(desc, ansi.DIM)}")

    if undeclared:
        print(
            f"\n  {ansi.c('Undeclared codes', ansi.YELLOW)} "
            f"{ansi.c('(used by ECUs but absent from can_buses.yaml):', ansi.DIM)}"
        )
        for code in undeclared:
            print(f"    {ansi.c(code, ansi.YELLOW)}  {per_bus[code]} ECU(s)")

    if n_unbussed:
        print(f"\n  {ansi.c(f'{n_unbussed} ECU(s) have no can_bus set.', ansi.DIM)}")

    if n_gateway:
        plural = "s" if n_gateway != 1 else ""
        print(
            f"\n  {ansi.c(f'{n_gateway} gateway ECU{plural} on `{ALL_CODE}` counted on every segment it bridges.', ansi.DIM)}"
        )

    print(f"\n  {ansi.c(f'source: {prof.can_buses_file}', ansi.DIM)}\n")
    return 0


def _upsert(args) -> int:
    # deferred: loading the bus editor at parser build adds a module to every invocation.
    from canair.yaml_edit.can_buses import upsert_bus

    with edit_scope(args):
        path = upsert_bus(
            args.code,
            profile=active(),
            name=args.name,
            description=args.description,
            bitrate=args.bitrate,
            private=args.private,
        )
    echo_edit(f"updated bus {args.code}", path, file=sys.stderr)
    if args.json:
        json.dump({"code": args.code, "source": str(path)}, sys.stdout)
        print()
    return 0
