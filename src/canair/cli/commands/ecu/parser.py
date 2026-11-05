"""The ``canair ecu`` parser: the group and its ``show`` kind."""

from __future__ import annotations

import argparse

from canair.analysis.ecu_summary import SORT_COLUMNS
from canair.cli.args.group import group_help
from canair.cli.commands._hints import ecu_completer as _ecu_completer

from . import add, rename, revisions
from .epilog import EPILOG
from .show import run

NAME = "ecu"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Inspect ECUs (list/detail), add, rename, or edit revisions: "
        "show | add | rename | revisions | set-variants",
        description="Inspect or edit the profile's ECU registry.\n"
        "  show          list ECUs, or show one ECU's details and PID stats (default)\n"
        "  add           register a new ECU in the active profile's ecus/ (offline)\n"
        "  rename        rename an ECU (rewrites its key and ecus/ file)\n"
        "  revisions     list or edit an ECU's revisions (protocol/decoding versions)\n"
        "  set-variants  say which variants an ECU is fitted to\n\n"
        "A bare `canair ecu` or `canair ecu BMS` is shorthand for `canair ecu show …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    kinds = parser.add_subparsers(dest="ecu_kind", metavar="<kind>")
    _add_show_parser(kinds)
    add.add_parser(kinds)
    rename.add_parser(kinds)
    revisions.add_parsers(kinds)
    parser.set_defaults(func=group_help("_ecu_group_parser"), _ecu_group_parser=parser)
    return parser


def _add_show_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "show",
        help="List ECUs, or show one ECU's details and PID stats",
        description="List ECUs, or show one ECU's details and PID stats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "ecu",
        nargs="?",
        help="ECU name, alias, or hex TX/RX id (omit to list all)",
    ).completer = _ecu_completer
    parser.add_argument(
        "view",
        nargs="?",
        choices=["pids", "edit"],
        help="'pids': compact per-PID view with each PID's latest decoded state "
        "(e.g. `canair ecu BMS pids`); "
        "'edit': open the ECU's ecus/ YAML file in $EDITOR (TTY only — agents "
        "must use `canair pids` instead; e.g. `canair ecu HVAC edit`)",
    )
    parser.add_argument(
        "--sort",
        choices=list(SORT_COLUMNS),
        default="bus",
        help="List ordering: 'bus' (default; group by CAN segment) or by column: "
        "name/tx/proto/states (ascending), pids/verif/caps (descending)",
    )
    parser.add_argument(
        "--states",
        action="store_true",
        help="Add a STATES column: the vehicle states each ECU is readable/awake in "
        "(ECU-level vehicle_states, else the union of its PIDs')",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(func=run)
    return parser
