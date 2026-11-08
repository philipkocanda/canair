"""Flag parsing and the confirmation both memory reads (``scan memory``, ``memory dump``) share.

A read of a peripheral register can change an ECU's state on some microcontrollers, so
neither command has a default range and both ask before the first read.
"""

from __future__ import annotations

import argparse

from canair.core.id_ranges import parse_id_ranges
from canair.core.prompt import confirm
from canair.render import messages
from canair.uds.memory import MemoryRequestError, parse_format

RISK = (
    "Reading memory is a read, but on some microcontrollers reading a peripheral register "
    "clears a flag or pops a queue. Name only ranges from the chip's memory map."
)


def address_ranges(value: str) -> tuple[tuple[int, int], ...]:
    """``--range START-END[,…]`` (hex addresses) for argparse."""
    try:
        return parse_id_ranges(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def memory_format(value: str) -> str:
    try:
        parse_format(value)
    except MemoryRequestError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return value


def hex_int(value: str) -> int:
    try:
        n = int(value, 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a hex number") from None
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def add_read_args(parser: argparse.ArgumentParser, *, throttle_ms: int = 20) -> None:
    """The flags every memory read takes: format, session, throttle and the confirmation."""
    parser.add_argument(
        "--format",
        type=memory_format,
        metavar="ALFID|kwp",
        help="Memory format when the ECU declares none: a UDS ALFID (24 = 2-byte size, "
        "4-byte address) or kwp. Saved by `canair memory set` or `canair scan memory`.",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Open a diagnostic session first (--mode, else the ECU's memory.session, else 03)",
    )
    parser.add_argument(
        "--mode", dest="session_mode", metavar="HEX", help="Session mode for --session"
    )
    parser.add_argument(
        "--throttle-ms",
        type=int,
        default=throttle_ms,
        help=f"Delay in ms between reads (default {throttle_ms})",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation before the first read"
    )


def confirm_reads(args: argparse.Namespace, verb: str) -> bool:
    """Ask before ``verb`` reads ``args.ranges`` of ``args.ecu``; False when declined."""
    spans = ", ".join(f"0x{a:X}-0x{b:X}" for a, b in args.ranges)
    if not args.yes:
        messages.warning(RISK)
    return confirm(f"{verb} {args.ecu} memory at {spans}?", yes=args.yes, danger=True)
