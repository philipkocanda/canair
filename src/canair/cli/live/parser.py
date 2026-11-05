"""The argparse flags every live subcommand shares, and how a command runs its operation.

Each live subcommand exposes only the arguments it needs, plus
:func:`add_connection_args`, and runs its own :class:`~.runtime.LiveOperation` through
:func:`live_command` (or :func:`~.runtime.run_live` from its own ``run``). The runtime reads
the connection flags through ``getattr``, so a parser exposes only what it means.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import partial

from canair.transport.config import DEFAULT_TRANSPORT, VALID_TRANSPORTS

from .operation import LiveOperation
from .runtime import run_live


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add the connection/output flags common to every live subcommand."""
    from canair.config import default_wican, wican_addresses

    parser.add_argument(
        "--wican",
        default=None,
        help=f"WiCAN address: {', '.join(wican_addresses())} or IP "
        f"(default: config transport.host / default_wican={default_wican()})",
    )
    parser.add_argument(
        "--transport",
        choices=VALID_TRANSPORTS,
        default=None,
        help="CAN transport: slcan-tcp (raw CAN), wican-ws (WiCAN ELM327 "
        "WebSocket), or elm327-tcp (direct ELM327 adapter over TCP). "
        f"Overrides the config `transport.type` (default: {DEFAULT_TRANSPORT}).",
    )
    parser.add_argument(
        "--no-fallback",
        dest="no_fallback",
        action="store_true",
        help="Don't auto-fall-back to other configured devices when the selected "
        "one is unreachable (see config transport.fallback).",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Keep retrying to reach the device indefinitely, then start as soon "
        "as it comes online (Ctrl-C to stop). For 'monitor', also reconnects "
        "forever if the connection drops mid-session (auto-failover to another "
        "same-transport device is bounded by default; --wait makes it unbounded).",
    )
    parser.add_argument(
        "--elm-timeout",
        type=int,
        default=None,
        metavar="MS",
        help="ELM327 ECU response timeout in ms (sent as ATSTxx after init)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Overall UDS response timeout in seconds (default 3.0 ELM / 2.0 raw). "
        "Overrides any per-ECU response_timeout_ms for the whole run.",
    )
    parser.add_argument(
        "--no-learn-frames",
        dest="no_learn_frames",
        action="store_true",
        help="Don't write response_frames back into the profile's ecus/ when this "
        "session confirms how many CAN frames a PID's response occupies (the "
        "count that lets the adapter answer without waiting out its full ECU "
        "timeout). Use when reading a car the active profile doesn't describe.",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show raw transport traffic and expressions"
    )
    parser.add_argument(
        "--timings",
        action="store_true",
        help="Print per-ECU/PID round-trip timing stats on exit (to stderr)",
    )
    parser.add_argument(
        "--reboot",
        action="store_true",
        help="Reboot WiCAN after session to restore AutoPID mode (skipped on firmware v6, "
        "which resumes AutoPID by itself)",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Bypass dangerous command blocklist (requires explicit per-command consent)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ask a session already holding the connection to release it, then wait for it",
    )
    # This command takes the device connection lock itself, and reports a contended one itself.
    parser.set_defaults(takes_device_lock=True)


def live_command(operation: LiveOperation) -> Callable[[argparse.Namespace], int]:
    """The ``func`` of a subcommand that runs ``operation`` on the device as it is."""
    return partial(run_live, operation=operation)
