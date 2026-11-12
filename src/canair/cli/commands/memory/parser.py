"""The ``canair memory`` parser: the group, and the ``dump`` kind."""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help
from canair.cli.live import add_connection_args, ecu_completer, run_live
from canair.cli.live.handlers import memory as handlers
from canair.core.prompt import confirm
from canair.render import messages

from .reads import (
    RISK,
    add_read_args,
    address_ranges,
    confirm_reads,
    default_space_text,
    ecu_format,
    hex_int,
    spans_text,
)
from .views import add_offline_parsers

NAME = "memory"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Read ECU memory (0x23) into local dumps: image | dump | ls | show | diff | "
        "export | set",
        description="Read ECU memory with ReadMemoryByAddress (0x23) into dumps kept on this "
        "machine, and list, compare and export them. Choose a kind:\n"
        "  image   plan and read an ECU's whole memory image, resumably (talks to the car)\n"
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
    _add_image_parser(kinds)
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
        "caught by its length; a block that does not come back whole is halved, after one "
        "more try when the ECU's max_block says that size works. The block grows back, "
        "doubling after 500 clean reads, never above --block (or max_block); errors add "
        "throttle, which falls back to --throttle-ms and no lower. A refused "
        "range is recorded as a gap with its NRC, and a locked one (securityAccessDenied) is "
        "left alone, and so is one that refuses by silence. The dump is written to disk as it "
        "is read, so a keypress (s or escape), Ctrl-C or a killed process keeps what was "
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
    add_read_args(dump, throttle_ms=0)
    add_connection_args(dump)
    dump.set_defaults(func=run_dump)


def run_dump(args: argparse.Namespace) -> int:
    if not confirm_reads(args, "Dump"):
        return 1
    return run_live(args, handlers.DUMP)


def _add_image_parser(kinds) -> None:
    image = kinds.add_parser(
        "image",
        help="Plan and read an ECU's whole memory image, resumably and checked",
        description="Read the readable memory of one ECU into one dump, unattended. The run "
        "maps the space (the readable regions ecus/ records for its software; else --range or "
        "the whole space of a memory format with addresses of 3 bytes or fewer, mapped like "
        "`scan memory` and written back to ecus/), finds the flash size and any aliasing, "
        "surveys which areas hold data, shows the plan with its time estimate and asks before "
        "it reads (--yes to skip). It stops after the map when the ECU answers every address of "
        "that default space: such an ECU does not check the range, so name its memory with "
        "--range. sparse reads the data areas in "
        "full and samples the erased ones; --thorough reads every byte twice. The block adapts "
        "to errors within the ECU's max_block and --block-limit. A silent ECU, a failed "
        "health or state check or a dropped link pauses the job; it reads on by itself when "
        "the checks pass within --wait-minutes, and is otherwise saved for --resume. A "
        "resume needs the same software build and re-reads the first and the last two "
        "extents first. The report has the stability checks, the fault codes against the "
        "baseline of the ECU and its bus neighbours, and where the ECU's identity strings sit "
        "in the image. Only ReadMemoryByAddress reads memory; nothing is written.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair memory image VCU --state CHARGING --plan-only
  canair memory image VCU --state CHARGING --label "vcu flash"
  canair memory image MCU --range 000000-0FFFFF --thorough
  canair memory image --resume VCU/20261108-101500-vcu-flash""",
    )
    image.add_argument(
        "ecu", nargs="?", metavar="ECU", help="ECU name or TX id (not with --resume)"
    ).completer = ecu_completer
    image.add_argument(
        "--resume", metavar="DUMP", help="Read on in an unfinished job (id, prefix or path)"
    )
    image.add_argument(
        "--thorough",
        action="store_true",
        help="Read every readable byte, twice (default: sparse, the data areas in full and "
        "the erased areas sampled)",
    )
    image.add_argument(
        "--range",
        dest="ranges",
        type=address_ranges,
        metavar="START-END[,…]",
        help="Map these hex ranges first (default: the readable regions ecus/ records for "
        "the ECU's software, else the format's whole space if it has addresses of 3 bytes or "
        "fewer)",
    )
    image.add_argument(
        "--step",
        type=hex_int,
        metavar="HEX",
        help="Map: probe every HEX bytes before bisecting (default 1000 with --range; about 256 "
        "probes across the default space)",
    )
    image.add_argument(
        "--stride",
        type=hex_int,
        default=0x1000,
        metavar="HEX",
        help="Survey one sample every HEX bytes (default 1000)",
    )
    image.add_argument(
        "--block-limit",
        type=int,
        metavar="N",
        help="Largest read (default and maximum: the ECU's memory.max_block, else 64)",
    )
    image.add_argument(
        "--state",
        dest="required_state",
        metavar="STATE",
        help="A vehicle state that must hold while the run reads (its when: predicate)",
    )
    image.add_argument(
        "--health",
        metavar="PID",
        help="The ECU PID read to check its health (default: its first active PID)",
    )
    image.add_argument(
        "--health-every",
        type=float,
        default=30.0,
        metavar="S",
        help="Check health and state every S seconds while reading (default 30)",
    )
    image.add_argument(
        "--wait-minutes",
        type=float,
        default=15.0,
        metavar="M",
        help="How long a paused run waits for the checks to pass again (default 15)",
    )
    image.add_argument("--label", dest="dump_label", metavar="TEXT", help="A label for the dump")
    image.add_argument(
        "--plan-only", action="store_true", help="Map and survey, print the plan, read nothing"
    )
    image.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write a new map or block size to the ECU's memory: section in ecus/",
    )
    add_read_args(image, throttle_ms=0)
    add_connection_args(image)
    image.set_defaults(func=run_image)


def _image_question(args: argparse.Namespace) -> str:
    """What the image reads before its plan is shown, in the words of the confirmation."""
    if args.resume:
        return f"Resume {args.resume} (after the same checks as a new run)"
    record = "" if args.dry_run else " and records the map in ecus/"
    if args.ranges:
        return f"Map {args.ecu} memory at {spans_text(args.ranges)}{record}, then survey it"
    space = default_space_text(ecu_format(args), args.step)
    if space is None:
        return (
            f"Survey {args.ecu} memory from the readable regions ecus/ records (with none for its "
            "software, it stops: its address space needs --range)"
        )
    return (
        f"Survey {args.ecu} memory (with no readable region recorded for its software, it maps "
        f"{space} first{record}; the plan is shown before the reads)"
    )


def run_image(args: argparse.Namespace) -> int:
    if (args.ecu is None) == (args.resume is None):
        messages.fail("name an ECU, or pass --resume DUMP", code=2)
    if not args.yes:
        messages.warning(RISK)
    if not confirm(f"{_image_question(args)}?", yes=args.yes, danger=True):
        return 1
    # deferred: the image run loads 9 modules (planner, rate, verifier, job reader) that no
    # other command needs, and building the parser would pay for them on every command
    from canair.cli.live.handlers.memory_image import IMAGE

    return run_live(args, IMAGE)
