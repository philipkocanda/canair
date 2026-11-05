"""``canair scan range``: resolve the arguments, maybe ask, then scan."""

from __future__ import annotations

import sys

from canair.cli.live import run_live
from canair.cli.live.handlers import scans
from canair.render import messages

from .plan import resolve_plan
from .wizard import run_wizard


def run_range(args) -> int:
    """Resolve friendly/absent args, optionally run the wizard, then scan."""
    want_wizard = args.interactive or args.tx is None
    if want_wizard:
        if not sys.stdin.isatty():
            if args.tx is None:
                messages.error(
                    "no ECU given. Specify one (e.g. `canair scan BMS`), run "
                    "`canair scan range` in a terminal for the wizard, or `canair discover` "
                    "to find live ECUs."
                )
                return 2
            # -i without a TTY: fall through to non-interactive resolution.
        else:
            if not run_wizard(args):
                return 1

    resolve_plan(args)
    return run_live(args, scans.RANGE)
