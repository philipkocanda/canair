"""The ``canair wican`` parser: the command and its two groups."""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help

from . import autopid, mode
from .epilog import EPILOG

NAME = "wican"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Build/sync the WiCAN AutoPID profile (autopid …, mode …)",
        description="Build and sync the WiCAN device's AutoPID profile.\n\n"
        "Nothing is written until you ask for it — a bare `canair wican` prints "
        "this help. Choose a subcommand:\n"
        "  autopid write     generate the v4 and v6 AutoPID JSON to the bundle's out/\n"
        "  autopid upload    generate + upload to the device (Pro)\n"
        "  autopid download  download the device's current AutoPID JSON (Pro)\n"
        "  autopid diff      download + diff against the generated JSON (Pro)\n"
        "  autopid stats     per-ECU/PID statistics table\n"
        "  mode show         show the device's active protocol\n"
        "  mode set MODE     switch the device protocol/mode, reboot + align transport (Pro)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    groups = parser.add_subparsers(dest="wican_command", metavar="<command>")

    autopid.add_parsers(groups)
    mode.add_parsers(groups)

    parser.set_defaults(
        func=run, _wican_func=group_help("_wican_group_parser"), _wican_group_parser=parser
    )
    return parser


def run(args) -> int:
    return args._wican_func(args)
