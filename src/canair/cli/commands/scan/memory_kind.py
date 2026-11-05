"""``canair scan memory``: map which address ranges an ECU answers memory reads in."""

from __future__ import annotations

import argparse

from canair.cli.commands.memory.reads import (
    add_read_args,
    address_ranges,
    confirm_reads,
    hex_int,
)
from canair.cli.live import add_connection_args, ecu_completer, run_live
from canair.cli.live.handlers import memory as handlers
from canair.live.memory.scan import DEFAULT_BLOCK_LIMIT


def add_memory_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "memory",
        help="Map which address ranges answer ReadMemoryByAddress (0x23)",
        description="Map which address ranges of one ECU answer memory reads. The scan "
        "reads one byte every --step bytes, bisects each change to its exact address, then "
        "finds the largest block that comes back whole inside a readable region, doubling "
        "from 16 bytes up to --block-limit and stopping at the first that fails. It stops "
        "on serviceNotSupported, on securityAccessDenied (canair never unlocks), on "
        "conditionsNotCorrect to every read, and when the ECU stops answering. The map, the "
        "format and the block are written to the ECU's memory: section (--dry-run: not). "
        "There is no default range: name ranges from the microcontroller's memory map. "
        "Asks before the first read (--yes to skip).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair scan memory BCM --range FEDE0000-FEDFFFFF\n"
        "  canair scan memory HVAC --range 000000-00FFFF --format 13 --step 100\n"
        "  canair scan memory MCU --range 000000-0FFFFF --step 10000 --no-block\n",
    )
    parser.add_argument("ecu", metavar="ECU", help="ECU name or TX id").completer = ecu_completer
    parser.add_argument(
        "--range",
        dest="ranges",
        type=address_ranges,
        required=True,
        metavar="START-END[,…]",
        help="Hex address ranges to map (required)",
    )
    parser.add_argument(
        "--step",
        type=hex_int,
        default=0x1000,
        metavar="HEX",
        help="Probe every HEX bytes before bisecting (default 1000)",
    )
    parser.add_argument(
        "--no-block", action="store_true", help="Skip the search for the largest block"
    )
    parser.add_argument(
        "--block-limit",
        type=int,
        default=DEFAULT_BLOCK_LIMIT,
        metavar="N",
        help=f"Largest block the search tries (default {DEFAULT_BLOCK_LIMIT}). Some ECUs hang on "
        "a read larger than they support until their supply is cycled; raise it with care",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the map but do not write it to ecus/"
    )
    add_read_args(parser)
    add_connection_args(parser)
    parser.set_defaults(func=run_memory_scan)
    return parser


def run_memory_scan(args: argparse.Namespace) -> int:
    if not confirm_reads(args, "Scan"):
        return 1
    return run_live(args, handlers.SCAN)
