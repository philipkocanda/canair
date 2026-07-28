"""``canair bus`` — list the profile's CAN bus segments.

Prints each physical CAN bus segment declared in the active profile's
``can_buses.yaml`` with its human name, description, and the number of ECUs
sitting on it (an ECU spanning two segments counts on each). The bus vocabulary
is vendor-specific (Hyundai/Kia B/P/C/M/H, Ford HS/MS, BMW PT-CAN/K-CAN, …), so
it lives per profile — this is the read-only view of it.

Examples:
  canair bus            # table of buses + descriptions + ECU counts
  canair bus --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys

NAME = "bus"

# ANSI colors — emitted only when stdout is a TTY (piped output stays plain).
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def _use_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    """Wrap ``text`` in an ANSI ``code`` when stdout is a TTY, else return it plain."""
    return f"{code}{text}{_RESET}" if _use_color() else text


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
        help="List the profile's CAN bus segments (names, descriptions, ECU counts)",
        description="List the active profile's CAN bus segments with their human "
        "names, descriptions, and the number of ECUs on each (from can_buses.yaml).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    from collections import Counter

    from canlib.can_buses import load_can_buses
    from canlib.ecus import load_ecus
    from canlib.profile import active

    prof = active()
    buses = load_can_buses(prof)
    ecus = load_ecus()

    # Count ECUs per bus code (an ECU spanning two segments counts on each).
    per_bus: Counter = Counter()
    n_unbussed = 0
    for info in ecus.values():
        if not isinstance(info, dict):
            continue
        codes = _normalize_bus(info.get("can_bus"))
        if not codes:
            n_unbussed += 1
            continue
        for code in codes:
            per_bus[code] += 1

    # Codes referenced by an ECU but not declared in the vocabulary (surfaced so
    # a typo/undeclared segment isn't silently invisible).
    declared = {b.code for b in buses}
    undeclared = sorted(set(per_bus) - declared)

    records = [
        {
            "code": b.code,
            "name": b.name or None,
            "description": b.description or None,
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
            f"\n  No CAN buses declared for profile {_c(prof.name, _CYAN)} "
            f"{_c('(no can_buses.yaml)', _DIM)}.\n"
            f"  Declare the vocabulary in {prof.can_buses_file}.\n"
        )
        return 0

    print(f"\n  {_c('CAN buses', _BOLD)} — {len(buses)} segment(s) in {_c(prof.name, _CYAN)}\n")
    header = f"{'CODE':<6} {'ECUS':>4}  {'NAME':<22} DESCRIPTION"
    print(f"  {_c(header, _DIM)}")
    for r in records:
        code = _c(f"{r['code']:<6}", _CYAN)
        n = r["ecus"]
        n_str = f"{n:>4}" if n else _c(f"{0:>4}", _YELLOW)
        name = str(r["name"] or "—")[:22]
        desc = str(r["description"] or "")
        print(f"  {code} {n_str}  {name:<22} {_c(desc, _DIM)}")

    if undeclared:
        print(
            f"\n  {_c('Undeclared codes', _YELLOW)} "
            f"{_c('(used by ECUs but absent from can_buses.yaml):', _DIM)}"
        )
        for code in undeclared:
            print(f"    {_c(code, _YELLOW)}  {per_bus[code]} ECU(s)")

    if n_unbussed:
        print(f"\n  {_c(f'{n_unbussed} ECU(s) have no can_bus set.', _DIM)}")

    print(f"\n  {_c(f'source: {prof.can_buses_file}', _DIM)}\n")
    return 0
