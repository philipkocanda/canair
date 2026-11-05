"""``canair wican mode``: show or set the device protocol."""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help
from canair.core.prompt import confirm
from canair.device.wican.api import resolve_wican_url
from canair.render import messages
from canair.transport.config import TransportType

from .common import add_wican_arg, require_pro, require_v4_api

PROTOCOLS = ("elm327", "slcan", "savvycan", "realdash66", "auto_pid")


# canair drives the bus in exactly two device modes; `mode set` aligns the
# config transport.type to match so switching the device doesn't leave the
# transport pointing at the wrong backend (the usual foot-gun). The other modes
# (auto_pid/savvycan/realdash66) have no request/response transport, so the
# transport is left untouched with a note.
MODE_TO_TRANSPORT: dict[str, TransportType] = {
    "slcan": "slcan-tcp",
    "elm327": "wican-ws",
}


def add_parsers(groups) -> argparse.ArgumentParser:
    parser = groups.add_parser(
        "mode",
        help="Show or set the WiCAN device protocol/mode",
        description="Inspect or switch the WiCAN device's operating protocol "
        "(elm327/slcan/auto_pid/…). This is the device's own mode, distinct "
        "from the AutoPID profile. Firmware 4.x only: v6 has no protocol mode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode_command", metavar="<action>")

    show = sub.add_parser(
        "show",
        help="Show the device's active protocol",
        description="Report the WiCAN device's currently active protocol/mode.",
    )
    add_wican_arg(show)
    show.set_defaults(_wican_func=cmd_mode_show)

    setp = sub.add_parser(
        "set",
        help="Switch the device protocol/mode and reboot (Pro)",
        description=(
            "Switch the WiCAN device to MODE and reboot it, then align canair's "
            "transport to match (slcan -> slcan-tcp, elm327 -> wican-ws). "
            "WiCAN Pro on firmware 4.x only.\n\n"
            "Valid modes:\n"
            "  slcan        raw CAN — canair drives ISO-TP client-side; enables sniff\n"
            "  elm327       ELM327 terminal — the dongle runs ISO-TP\n"
            "  auto_pid     restore Home Assistant AutoPID broadcasting\n"
            "  savvycan     stream frames to SavvyCAN\n"
            "  realdash66   stream frames to RealDash (CAN 66)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No metavar override: argparse then lists the valid modes in the usage line
    # (e.g. `{elm327,slcan,savvycan,realdash66,auto_pid}`) instead of a bare MODE.
    setp.add_argument("protocol", choices=PROTOCOLS, help="Target protocol/mode")
    setp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    setp.add_argument(
        "--no-transport",
        action="store_true",
        help="Don't auto-align the config transport.type to the new mode",
    )
    add_wican_arg(setp)
    setp.set_defaults(_wican_func=cmd_mode_set)

    parser.set_defaults(_wican_func=group_help("_wican_group_parser"), _wican_group_parser=parser)
    return parser


def cmd_mode_show(args) -> int:
    from canair.device.wican.mode import current_protocol

    base_url = resolve_wican_url(args.wican)
    guard = require_v4_api(base_url, "wican-mode")
    if guard is not None:
        return guard
    try:
        proto = current_protocol(base_url)
    except Exception as e:
        messages.error(f"cannot reach WiCAN at {base_url}: {e}")
        return 1
    print(f"WiCAN protocol: {proto or '?'}")
    return 0


def sync_transport_to_mode(mode: str, *, no_transport: bool) -> None:
    """Align the config ``transport.type`` to the device mode just set.

    canair drives the bus in ``slcan`` (raw ``slcan-tcp``) or ``elm327``
    (``wican-ws`` ELM327 terminal); switching the device mode without also
    pointing the transport at it is the usual foot-gun. Prints the ``old ->
    new`` transition, or reports the transport is already aligned. Modes with no
    request/response transport (auto_pid/savvycan/realdash66) are left untouched.
    """
    if no_transport:
        return

    desired = MODE_TO_TRANSPORT.get(mode)
    if desired is None:
        print(
            f"  Transport unchanged — no canair transport maps to '{mode}' mode "
            "(canair drives the bus in 'slcan' or 'elm327' mode)."
        )
        return

    from canair.config import get_config_key, set_config_key
    from canair.transport.config import DEFAULT_TRANSPORT

    current = get_config_key("transport.type") or DEFAULT_TRANSPORT
    if current == desired:
        print(f"  Transport already '{desired}'.")
        return
    set_config_key("transport.type", desired)
    print(f"  Transport '{current}' → '{desired}'.")


def cmd_mode_set(args) -> int:
    """Explicitly set the WiCAN device protocol/mode (reboots). Opt-in only."""
    from canair.device.wican.mode import ModeError, current_protocol, set_protocol

    guard = require_pro("mode set")
    if guard is not None:
        return guard

    base_url = resolve_wican_url(args.wican)
    guard = require_v4_api(base_url, "wican-mode")
    if guard is not None:
        return guard
    target = args.protocol
    try:
        cur = current_protocol(base_url)
    except Exception as e:
        messages.error(f"cannot reach WiCAN at {base_url}: {e}")
        return 1

    if cur == target:
        print(f"WiCAN already in '{target}' mode.")
        sync_transport_to_mode(target, no_transport=args.no_transport)
        return 0

    question = (
        f"Switch WiCAN from '{cur}' to '{target}'? This reboots the device "
        "(~5s) and interrupts its current mode."
    )
    if not confirm(question, yes=args.yes, danger=True):
        return 1

    print(f"Switching WiCAN '{cur}' → '{target}' (rebooting)…")
    try:
        set_protocol(base_url, target)
    except ModeError as e:
        messages.error(str(e))
        return 1
    try:
        now = current_protocol(base_url)
    except Exception:
        now = "?"
    if now == target:
        print(f"WiCAN now in '{target}' mode.")
        sync_transport_to_mode(target, no_transport=args.no_transport)
        return 0
    messages.warning(f"WiCAN reports '{now}' after switch")
    return 1
