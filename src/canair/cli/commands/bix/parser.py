"""The ``canair bix`` parser."""

from __future__ import annotations

import argparse

from .entry import run

NAME = "bix"


# ANSI colors (match the sibling tools: decode, coverage, research).
# Emitted only when stdout is a TTY so piped/redirected output stays plain.
_EPILOG = """\
run `canair bix` with no arguments for a guided overview (a legend explaining
each notation + a compact 2-frame table); `--table` prints the full table.

input formats:
  w9, W09     WiCAN byte index (prefix w)
  B09         WiCAN byte index (uppercase B — the Bnn convention)
  i6, i0x06   ISO-TP payload index (prefix i)
  b32         OBDb bix / bit index (lowercase b)
  E, AA       Torque letter notation (Torque app, Car Scanner & similar apps)
  9           Plain number (assumed WiCAN)

subfunction modes:
  -1          1-byte subfunction (21xx PIDs) — default
  -2          2-byte subfunction (22xxxx DIDs)

  with a `--pid` that names its service (`22xxxx` / `21xx`) the width is DERIVED
  from it, so `bix -a HEX --ecu IGPM --pid 22BC03` needs no -2; an explicit -1/-2
  still wins (and is flagged when it contradicts the PID). A short-form DID
  (`B004`) doesn't state its service — pass -2 yourself.

optional columns (--table / --annotate; both hidden by default):
  --torque    add the Torque letter column (Torque app, Car Scanner & similar)
  --obdb      add the OBDb bix (bit-index) column — a distinct notation

note: with --annotate/-a, put the mode flag (-1/-2) BEFORE the hex bytes,
      e.g. `bix -2 -a 62 01 A0 ...` — a mode flag after the bytes is
      consumed as another argument."""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations",
        description="Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument("value", nargs="?", help="Index to convert (see formats below)")
    # default=None (not 1) so run() can tell an EXPLICIT -1 from an unset flag and
    # fall back to deriving the width from --pid; _resolve_sub_bytes applies the 1.
    parser.add_argument(
        "-1",
        dest="sub_bytes",
        action="store_const",
        const=1,
        default=None,
        help="1-byte subfunction mode (default, unless derived from --pid)",
    )
    parser.add_argument(
        "-2",
        dest="sub_bytes",
        action="store_const",
        const=2,
        help="2-byte subfunction mode (22xxxx DIDs)",
    )
    parser.add_argument(
        "--table", "-t", action="store_true", help="Print the full conversion table (all frames)"
    )
    parser.add_argument(
        "--annotate",
        "-a",
        metavar="HEX",
        nargs="+",
        help="Annotate a hex payload with all index representations "
        "(e.g. 62B0047402990C0040A000AAAA, or space-separated bytes "
        "62 B0 04 ... quoted or unquoted). Expects the reassembled UDS "
        "response payload (SID-first, ISO-TP PCI stripped) unless --raw.",
    )
    parser.add_argument(
        "--raw",
        "--frame",
        dest="raw_frame",
        action="store_true",
        help="With --annotate: the hex is an ALREADY-FRAMED CAN payload (ISO-TP "
        "PCI bytes present, e.g. straight off the bus) — index it as-is instead "
        "of reconstructing the framing from a PCI-stripped UDS payload.",
    )
    parser.add_argument(
        "--torque",
        dest="show_torque",
        action="store_true",
        help="Show the Torque byte-letter column (--table/--annotate). Hidden by "
        "default. Torque notation (A, B, C… from the first UDS data byte) is what "
        "the Torque app, Car Scanner, and similar OBD apps use — handy for porting "
        "their PID sheets.",
    )
    parser.add_argument(
        "--obdb",
        dest="show_obdb",
        action="store_true",
        help="Show the OBDb bix (bit-index) column (--table/--annotate). Hidden by "
        "default. bix is a distinct notation from Torque (data-byte index × 8) — "
        "request it independently of --torque.",
    )
    parser.add_argument(
        "--no-legend",
        dest="legend",
        action="store_false",
        help="With --annotate: omit the trailing definition list of the Role terms "
        "(PCI/SID/DID/LID/RID/NRC/…) that appear in the payload.",
    )
    parser.add_argument(
        "--max", type=int, default=71, help="Max WiCAN index for table (default: 71)"
    )
    parser.add_argument(
        "--ecu",
        help="With --annotate: overlay which defined signal maps each byte "
        "(and flag unmapped bytes). Requires --pid.",
    )
    parser.add_argument(
        "--pid",
        help="With --annotate --ecu: the PID whose signals to overlay (e.g. 22BC03). "
        "Also sets the subfunction width when it names its service (22xxxx DID → 2 "
        "bytes, 21xx → 1) unless -1/-2 is passed explicitly.",
    )
    parser.set_defaults(func=run)
    return parser
