"""The ``canair memory`` parser: the group, and the ``dump`` kind."""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help
from canair.cli.live import add_connection_args, ecu_completer, run_live
from canair.cli.live.handlers import memory as handlers

from .reads import add_read_args, address_ranges, confirm_reads
from .views import add_offline_parsers

NAME = "memory"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Read ECU memory (0x23) into local dumps: dump | ls | show | diff | export | set",
        description="Read ECU memory with ReadMemoryByAddress (0x23) into dumps kept on this "
        "machine, and list, compare and export them. Choose a kind:\n"
        "  dump    read address ranges into a new dump (talks to the car)\n"
        "  ls      list the dumps\n"
        "  show    summarise a dump, or hex-dump a range of it\n"
        "  diff    the runs of addresses that differ between two dumps\n"
        "  export  write a dump as Intel HEX, S-record or binary\n"
        "  set     set an ECU's memory format, largest block or session\n\n"
        "Find which ranges an ECU answers with `canair scan memory`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
the loop that finds a RAM variable:
  canair scan memory BCM --range FEDE0000-FEDFFFFF
  canair memory dump BCM FEDE0000-FEDFFFFF --label "lights off"
  canair memory dump BCM FEDE0000-FEDFFFFF --label "lights on"
  canair memory diff BCM/20261105-2210-lights-off BCM/20261105-2212-lights-on
  canair pids upsert-param BCM 23/FEDE1234+02 LIGHT_STATE B04

Dumps can hold OEM software and secrets: they stay in your config directory and nothing
that shares a profile can reach them.""",
    )
    kinds = parser.add_subparsers(dest="memory_kind", metavar="<kind>")
    _add_dump_parser(kinds)
    add_offline_parsers(kinds)
    parser.set_defaults(func=group_help("_memory_group_parser"), _memory_group_parser=parser)
    return parser


def _add_dump_parser(kinds) -> None:
    dump = kinds.add_parser(
        "dump",
        help="Read address ranges into a local dump",
        description="Read address ranges of one ECU into a new dump. The block size "
        "alternates between N and N-1 bytes so a reply to the previous request is always "
        "caught by its length; a block that does not come back whole is halved. A refused "
        "range is recorded as a gap with its NRC, and a locked one (securityAccessDenied) is "
        "left alone. A keypress (s or escape) or Ctrl-C stops the dump and keeps what was "
        "read. Asks before the first read (--yes to skip).",
    )
    dump.add_argument("ecu", metavar="ECU", help="ECU name or TX id").completer = ecu_completer
    dump.add_argument(
        "ranges", type=address_ranges, metavar="START-END[,…]", help="Hex address ranges"
    )
    dump.add_argument(
        "--block",
        type=int,
        metavar="N",
        help="Bytes per read (default: the ECU's memory.max_block, else 64)",
    )
    dump.add_argument("--label", dest="dump_label", metavar="TEXT", help="A label for the dump")
    add_read_args(dump)
    add_connection_args(dump)
    dump.set_defaults(func=run_dump)


def run_dump(args: argparse.Namespace) -> int:
    if not confirm_reads(args, "Dump"):
        return 1
    return run_live(args, handlers.DUMP)
