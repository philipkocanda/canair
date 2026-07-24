"""``canair repl`` — interactive live terminal (the old no-mode default)."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "repl"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["interactive"],
        help="Interactive live terminal (REPL) for typing raw ELM327/UDS commands",
        description="Drop into an interactive live terminal (REPL) over the WiCAN\n"
        "connection — type raw ELM327 (AT...) and UDS requests by hand and see the\n"
        "decoded response, for exploratory poking the one-shot commands don't cover.\n\n"
        "Inside the REPL:\n"
        "  ATSH7E4         set the target ECU header (ELM327 AT command)\n"
        "  2101 / 22C00B   send a UDS request to the current header\n"
        "  !decode         decode the last response using the profile definitions\n"
        "  !hexdump        hex dump of the last response\n"
        "  !info <ECU>     show an ECU's info from the profile (e.g. !info BMS)\n"
        "  !list           list all known ECUs\n"
        "  !tester [id]    TesterPresent keepalive loop (Ctrl+C to stop)\n"
        "  !reboot         reboot the WiCAN to restore AutoPID mode\n"
        "  !quit / Ctrl+C  exit\n\n"
        "For scripted/one-shot reads prefer `canair query` instead; this is the\n"
        "manual, freeform fallback.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair repl                     # open the interactive terminal (default WiCAN)
  canair repl --wican vpn         # connect via the 'vpn' address
  canair interactive              # 'interactive' is an alias for 'repl'
""",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser
