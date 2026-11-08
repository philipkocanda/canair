"""Flag parsing and the confirmation both memory reads (``scan memory``, ``memory dump``) share.

A read of a peripheral register can change an ECU's state on some microcontrollers, so
both ask before the first read, naming what they read. ``memory dump`` has no default range;
``scan memory`` and ``memory image`` default to the whole space of a memory format with
addresses of 3 bytes or fewer (``plans/2026-11-08-memory-default-space.md``), and the
confirmation names it.
"""

from __future__ import annotations

import argparse

from canair.cli.profile_context import active
from canair.core.id_ranges import parse_id_ranges
from canair.core.prompt import confirm
from canair.ecus.pids import load_pids
from canair.live.memory.reads import MemoryTargetError, resolve_target
from canair.render import messages
from canair.uds.memory import (
    DEFAULT_SPACE_ADDRESS_BYTES,
    MemoryFormat,
    MemoryRequestError,
    default_scan_space,
    default_scan_step,
    parse_format,
)

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


def ecu_format(args: argparse.Namespace) -> MemoryFormat | None:
    """The memory format ``args.ecu``'s reads take: ``--format``, else the profile's."""
    if args.format:
        return parse_format(args.format)
    try:
        return resolve_target(load_pids(profile=active()), args.ecu).format
    except MemoryTargetError:
        return None


def default_space_text(fmt: MemoryFormat | None, step: int | None) -> str | None:
    """How the default space reads: the range and about how many probes, or ``None``.

    ``None`` when ``fmt`` is too wide to map whole. An unknown format is probed at 0 first.
    """
    if fmt is None:
        return (
            "the whole space of its memory format (probed at 0x0), if its addresses are "
            f"{DEFAULT_SPACE_ADDRESS_BYTES} bytes or fewer"
        )
    space = default_scan_space(fmt)
    if space is None:
        return None
    start, end = space
    every = step or default_scan_step(start, end)
    probes = (end - start) // every + 1
    return f"0x{start:X}-0x{end:X} (1-byte probes every 0x{every:X}, about {probes})"


def require_space(args: argparse.Namespace, fmt: MemoryFormat | None) -> str:
    """The space a read with no ``--range`` maps, as text; exits 2 when there is none."""
    text = default_space_text(fmt, args.step)
    if text is None:
        assert fmt is not None
        messages.fail(
            f"{args.ecu}'s memory format {fmt.name} has {fmt.address_bytes}-byte addresses, too "
            "wide to map whole: pass --range from the chip's memory map",
            code=2,
        )
    return text


def spans_text(ranges) -> str:
    return ", ".join(f"0x{a:X}-0x{b:X}" for a, b in ranges)


def confirm_reads(args: argparse.Namespace, verb: str, where: str | None = None) -> bool:
    """Ask before ``verb`` reads ``args.ecu`` at ``where`` (``args.ranges``); False if declined."""
    if not args.yes:
        messages.warning(RISK)
    where = where or spans_text(args.ranges)
    return confirm(f"{verb} {args.ecu} memory at {where}?", yes=args.yes, danger=True)
