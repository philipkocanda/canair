"""``canair discover`` — sweep TX addresses (or one functional request) to find ECUs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping

from canair.cli.live import add_connection_args, run_live
from canair.cli.live.handlers import scans
from canair.cli.live.handlers.ranges import parse_range
from canair.cli.profile_context import active
from canair.ecus.pids import load_pids
from canair.live.discover.physical import discovery_targets, fmt_id
from canair.profile.model import ProfileError
from canair.safety.blocklist import check_functional_request
from canair.transport.functional import CAN_FD_REFUSAL, classic_can
from canair.transport.isotp_params import build_isotp_params
from canair.uds.addressing import (
    DEFAULT_MODE,
    AddressingMode,
    functional_request_id,
    resolve_mode,
)

NAME = "discover"
ALIASES = ["disc"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Sweep a range of TX addresses to find responding ECUs",
        description=(
            "Sweep a range of TX addresses (sends 10 01 to each) to find ECUs. With "
            "--functional, send one read on the functional (broadcast) id instead and list "
            "every ECU that answers, grouped by bus: which buses a gateway car routes "
            "diagnostics to."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair discover                     Discover ECUs in 0x700-0x7EF (default)
  canair discover --range 600-6FF     Custom range
  canair discover --delay 0.5         Slower pacing
  canair discover --register          Auto-add newly-found ECUs to ecus/
  canair discover --register --dry-run  Preview what would be registered
  canair discover --identify          Read identity DIDs from each ECU found
  canair discover --functional        One functional 3E 00: every routed responder
  canair discover --functional --request 190208 --save
                                      Functional DTC read, saved to captures/
  canair discover --functional --repeat 3 --interval 1
                                      Three rounds 1 s apart (does a bus drop out?)

functional requests:
  A functional frame reaches every ECU the gateway routes it to at once, so only
  these reads may be sent, with or without --unsafe: 3E00, 22xxxx (one DID), 1Axx,
  1902xx, 1800FF00, 01xx (one OBD-II mode 01 PID). On Hyundai/Kia any functional
  frame also tells in-car testers to stop sending diagnostic requests of their own
  (ES95486): it changes what the car does, not only what canair sees.
  slcan-tcp is the complete view; an ELM327 transport (wican-ws, elm327-tcp) is
  best-effort, most of all for multi-frame replies.
""",
    )
    parser.add_argument(
        "--range",
        metavar="START-END",
        default=None,
        help="Address range (default: 700-7EF for 11-bit, 00-FF for 29-bit target bytes). "
        "The functional id 0x7DF is skipped; --functional sends on it",
    )
    parser.add_argument(
        "--delay", type=float, default=0.2, help="Delay between probes in seconds (default 0.2)"
    )
    functional = parser.add_argument_group("functional sweep")
    functional.add_argument(
        "--functional",
        action="store_true",
        help="Send one request on the functional id (0x7DF, or 0x18DB33F1 in a 29-bit mode) "
        "and list every responder, instead of sweeping physical ids",
    )
    functional.add_argument(
        "--request",
        metavar="HEX",
        default=None,
        help="With --functional: the read to broadcast (default 3E00; see below)",
    )
    functional.add_argument(
        "--window",
        type=float,
        default=None,
        metavar="SECONDS",
        help="With --functional: how long to collect replies (default 1.0)",
    )
    functional.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="With --functional: send the request N times (default 1)",
    )
    functional.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="With --functional --repeat: seconds between requests (default and minimum 1)",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="Register newly-discovered ECUs as files in the profile's ecus/ directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --register: show what would be added without writing",
    )
    parser.add_argument(
        "--identify",
        action="store_true",
        help="Run `canair identity` on each alive ECU after the sweep "
        "(otherwise offer interactively)",
    )
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument(
        "--label",
        metavar="TEXT",
        default=None,
        help="Session label for --save (asked for when omitted; with no terminal the "
        "session is saved under a generated label)",
    )
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    parser.set_defaults(func=run)
    return parser


def functional_args_error(args) -> str | None:
    """Why the functional-sweep flags are inconsistent, or None."""
    from canair.live.discover.functional import MIN_INTERVAL_S

    if not getattr(args, "functional", False):
        given = [
            flag
            for flag, set_ in (
                ("--request", args.request is not None),
                ("--window", args.window is not None),
                ("--repeat", args.repeat != 1),
                ("--interval", args.interval != 1.0),
            )
            if set_
        ]
        return f"{', '.join(given)} need --functional" if given else None
    if args.range is not None:
        return "--range sweeps physical ids; it can't be combined with --functional"
    if args.request is not None:
        blocked = check_functional_request(args.request)
        if blocked is not None:
            return blocked
    if args.window is not None and args.window <= 0:
        return "--window must be positive"
    if args.repeat < 1:
        return "--repeat must be at least 1"
    if args.interval < MIN_INTERVAL_S:
        return (
            f"--interval must be at least {MIN_INTERVAL_S:g} s: closer functional requests "
            "can silence the slower buses"
        )
    return None


def _profile_error() -> str | None:
    """Why the profile rules out a functional sweep (its addressing or CAN-FD), or None."""

    try:
        pids_data = load_pids(profile=active())
    except ProfileError:
        return None
    mode = resolve_mode(pids_data)
    if mode in (AddressingMode.NORMAL_EXTENDED_11BIT, AddressingMode.EXTENDED_29BIT):
        return f"--functional does not support the {mode.value} addressing mode"
    if mode == AddressingMode.NORMAL_29BIT:
        return (
            "--functional does not support the normal_29bit addressing mode: only "
            "normal_fixed_29bit replies (0x18DAF1xx) can be recognised"
        )
    isotp = pids_data.get("isotp")
    if not classic_can(build_isotp_params(dict(isotp) if isinstance(isotp, Mapping) else None)):
        return f"--functional: {CAN_FD_REFUSAL}"
    return None


def _range_error(args) -> str | None:
    """Why ``--range`` leaves the physical sweep nothing to probe, or None.

    Checked before connecting, so a bad range costs no connection. Only the functional id can
    empty a range, as :func:`canair.live.discover.discovery_targets` leaves it out.
    """
    if getattr(args, "functional", False) or args.range is None:
        return None
    try:
        addr_range = parse_range(args.range)
    except argparse.ArgumentTypeError as e:
        return str(e)
    try:
        mode = resolve_mode(load_pids(profile=active()))
    except ProfileError:
        mode = DEFAULT_MODE
    if discovery_targets(addr_range, mode):
        return None
    return (
        f"--range {args.range} holds only {fmt_id(functional_request_id(mode))}, the functional "
        "id, which the physical sweep skips; use --functional to send on it"
    )


def run(args) -> int:
    error = functional_args_error(args) or _range_error(args)
    if error is None and getattr(args, "functional", False):
        error = _profile_error()
    if error is not None:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return run_live(args, scans.DISCOVER)
