"""The ``canair status`` parser."""

from __future__ import annotations

import argparse

from .description import DESCRIPTION
from .gather import gather
from .render import render_status

NAME = "status"


ALIASES = ["st"]


def _valid_transports():
    from canair.transport.config import VALID_TRANSPORTS

    return VALID_TRANSPORTS


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Show the configured transport, device mode, and reachability",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair status                       # what am I talking to, in what mode, is it up?
  canair status --wican vpn           # check the device at the 'vpn' address
  canair status --no-fallback         # only the selected device, no fallback probe
  canair status --json                # machine-readable (for scripts/CI)

exit codes: 0 = reachable & usable, 1 = unreachable, 2 = misconfigured.
""",
    )
    parser.add_argument(
        "--transport",
        choices=_valid_transports(),
        default=None,
        help="Override the configured transport type",
    )
    parser.add_argument("--wican", default=None, help="Override device host (alias or IP)")
    parser.add_argument(
        "--no-fallback",
        dest="no_fallback",
        action="store_true",
        help="Report only the selected device — don't fall back to the other "
        "configured devices when it's unreachable (see config transport.fallback). "
        "Without it, status reports the device a live command would use.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    info = gather(args)
    if args.json:
        import json

        print(json.dumps(info, indent=2))
    else:
        render_status(info)
    return info["exit"]
